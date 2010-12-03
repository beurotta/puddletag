# -*- coding: utf-8 -*-

import pdb, os, glob, string, sys
sys.path.insert(0, '/home/keith/Documents/python/puddletag')

from copy import deepcopy
from functools import partial
from PyQt4.QtCore import *
from PyQt4.QtGui import *

from puddlestuff.constants import SAVEDIR, RIGHTDOCK, VARIOUS, MUSICBRAINZ
from puddlestuff.findfunc import filenametotag
from puddlestuff.puddleobjects import (ListBox, ListButtons, OKCancel, 
    PuddleConfig, PuddleThread, ratio, winsettings, natcasecmp)
import puddlestuff.resource
from puddlestuff.tagsources import RetrievalError
from puddlestuff.util import split_by_tag, to_string
from puddlestuff.webdb import strip
from audioinfo import PATH

#import exampletagsource, qltagsource

mutex = QMutex()

PROFILEDIR = os.path.join(SAVEDIR, 'masstagging')

#pyqtRemoveInputHook()

CONFIG = os.path.join(SAVEDIR, 'masstagging.conf')



NO_MATCH_OPTIONS = [
    unicode(QApplication.translate('Masstagging', 'Continue')),
    unicode(QApplication.translate('Masstagging', 'Stop'))]

SINGLE_MATCH_OPTIONS = [
    unicode(QApplication.translate('Masstagging', 'Combine and continue')),
    unicode(QApplication.translate('Masstagging', 'Replace and continue')),
    unicode(QApplication.translate('Masstagging', 'Combine and stop')),
    unicode(QApplication.translate('Masstagging', 'Replace and stop'))]

AMBIGIOUS_MATCH_OPTIONS = [
    unicode(QApplication.translate('Masstagging', 'Use best match')),
    unicode(QApplication.translate('Masstagging', 'Do nothing and continue'))]

COMBINE_CONTINUE = 0
REPLACE_CONTINUE = 1
COMBINE_STOP = 2
REPLACE_STOP = 3

CONTINUE = 0
STOP = 1

USE_BEST = 0
DO_NOTHING = 1
RETRY = 2

ALBUM_BOUND = 'album'
TRACK_BOUND = 'track'
PATTERN = 'pattern'
SOURCE_CONFIGS = 'source_configs'
FIELDS = 'fields'
JFDI = 'jfdi'
NAME = 'name'
DESC = 'description'
EXISTING_ONLY = 'field_exists'

DEFAULT_PROFILE = {
    ALBUM_BOUND: 50, 
    SOURCE_CONFIGS: [[MUSICBRAINZ, 0, 0, [], 0]],
    TRACK_BOUND: 80, 
    PATTERN: u'%artist% - %album%/%track% - %title%', 
    NAME: u'Default', 
    JFDI: True, 
    FIELDS: [u'artist', u'title'],
    DESC: u'',
    EXISTING_ONLY: False}

status_obj = QObject()

def set_status(msg): status_obj.emit(SIGNAL('statusChanged'), msg)

def to_list(value):
    if isinstance(value, (str, int, long)):
        value = [unicode(value)]
    elif isinstance(value, unicode):
        value = [value]
    return value

def combine(fields, info, retrieved, old_tracks):
    new_tracks = []
    for track in retrieved:
        info_copy = info.copy()
        info_copy.update(track)
        new_tracks.append(strip(info_copy, fields))
    return merge_tracks(old_tracks, new_tracks)

def config_str(config):
    return config[0]

def create_buddy(text, control, hbox=None):
    label = QLabel(text)
    label.setBuddy(control)

    if not hbox:
        hbox = QHBoxLayout()
    hbox.addWidget(label)
    hbox.addWidget(control, 1)
    
    return hbox

def fields_from_text(text):
    if not text:
        return []
    return filter(None, map(string.strip, text.split(u',')))

def find_best(matches, files, minimum=0.7):
    group = split_by_tag(files, 'album', 'artist')
    album = group.keys()[0]
    artists = group[album].keys()
    if len(artists) == 1:
        artist = artists[0]
    else:
        artist = VARIOUS
    d = {'artist': artist, 'album': album}
    scores = {}

    for match in matches:
        info = match[0]
        totals = [ratio(d[key].lower(), to_string(info[key]).lower()) 
            for key in d if key in info]
        if len(totals) == len(d):
            scores[min(totals)] = match
    
    if scores:
        max_ratio = max(scores)
        if max_ratio > minimum:
            return [scores[max_ratio]]
    else:
        return []

def load_config(filename = CONFIG):
    cparser = PuddleConfig(filename)
    info_section = 'info'
    name = cparser.get(info_section, NAME, '')
    numsources = cparser.get(info_section, 'numsources', 0)
    album_bound = cparser.get(info_section, ALBUM_BOUND, 70)
    track_bound = cparser.get(info_section, TRACK_BOUND, 80)
    match_fields = cparser.get(info_section, FIELDS, ['artist', 'title'])
    pattern = cparser.get(info_section, PATTERN, 
        '%artist% - %album%/%track% - %title%')
    jfdi = cparser.get(info_section, JFDI, True)
    desc = cparser.get(info_section, DESC, u'')
    existing = cparser.get(info_section, EXISTING_ONLY, u'')
    
    configs = []
    for num in range(numsources):
        section = 'config%s' % num
        get = lambda key, default: cparser.get(section, key, default)
        source = get('source', MUSICBRAINZ)
        no = get('no_match', 0)
        single = get('single_match', 0)
        fields = fields_from_text(get('fields', ''))
        many = get('many_match', 0)
        configs.append([source, no, single, fields, many])

    return {SOURCE_CONFIGS: configs, PATTERN: pattern, 
        ALBUM_BOUND: album_bound, TRACK_BOUND: track_bound,
        FIELDS: match_fields, JFDI: jfdi, NAME: name, DESC: desc,
        EXISTING_ONLY: existing}

def load_profiles(self, dirpath = PROFILEDIR):
    profiles = [load_config(conf) for conf in glob.glob(dirpath + u'/*.conf')]
    try:
        order = open(os.path.join(dirpath, 'order'), 'r').read().split('\n')
    except EnvironmentError:
        return profiles

    order = [z.strip() for z in order]
    first = []
    last=[]
    
    names = dict([(profile[NAME], profile) for profile in profiles])
    profiles = [names[name] for name in order if name in names]
    profiles.extend([names[name] for name in names if name not in order])

    return profiles

def match_files(files, tracks, minimum = 0.7, keys = None, jfdi=False, existing=False):
    if not keys:
        keys = ['artist', 'title']
    ret = {}
    unmatched = []
    for f in files:
        scores = {}
        for track in tracks:
            totals = [ratio(to_list(f.get(key, u'a'))[0].lower(), 
                to_list(track.get(key, u'b'))[0].lower()) 
                for key in keys]
            scores[min(totals)] = track
        if scores:
            max_ratio = max(scores)
            if max_ratio > minimum and f['__file'] not in ret:
                ret[f['__file']] = scores[max_ratio]
    if jfdi:
        sort_func = lambda f: f[PATH]
        audios = sorted([f['__file'] for f in files], natcasecmp, sort_func)
        sort_func = lambda t: to_string(t['track']) if 'track' in t \
            else to_string(t.get('title'))
        tracks = sorted(tracks, natcasecmp, sort_func)

        for audio, retrieved in zip(audios, tracks):
            if audio in ret:
                continue
            else:
                ret[audio] = retrieved
    
    if existing:
        previews = []
        for f, r in ret.items():
            temp = {}
            for field in r:
                if field not in f:
                    temp[field] = r[field]
            ret[f] = temp
    return ret

def merge_tracks(old_tracks, new_tracks):
    if not old_tracks:
        return new_tracks
    if not new_tracks:
        return old_tracks
    
    sort_func = lambda track: to_string(track['track']) if 'track' in \
        track else to_string(track.get('title', u''))
        
    old_tracks = sorted(old_tracks, natcasecmp,  sort_func)
    new_tracks = sorted(new_tracks, natcasecmp,  sort_func)

    for old, new in zip(old_tracks, new_tracks):
        for key in old.keys() + new.keys():
            if key in new and key in old:
                old[key] = to_list(old[key])
                old[key].extend(to_list(new[key]))
            elif key in new:
                old[key] = to_list(new[key])
    if len(new_tracks) > len(old_tracks):
        old_tracks.extend(new_tracks[len(old_tracks):])
    return old_tracks

def retrieve(results, album_bound = 0.7):
    tracks = []
    info = {}
    for tagsource, matches, files, config in results:
        fields = config[3]
        if not matches:
            operation = config[1]
            if operation == CONTINUE:
                set_status(QApplication.translate('Masstagging', '<b>%1</b>: No matches, trying other sources.').arg(tagsource.name))
                continue
            elif operation == STOP:
                set_status(QApplication.translate('Masstagging', '<b>%1</b>: No matches, stopping retrieval.').arg(tagsource.name))
                break
        elif len(matches) > 1:
            operation = config[4]
            if operation == DO_NOTHING:
                set_status(QApplication.translate('Masstagging', '<b>%1</b>: Inexact matches found, doing nothing.').arg(tagsource.name))
                continue
            elif operation == USE_BEST:
                set_status(QApplication.translate('Masstagging', '<b>%1</b>: Inexact matches found, using best.').arg(tagsource.name))
                matches = find_best(matches, files, album_bound)
                if not matches:
                    set_status(QApplication.translate('Masstagging', '<b>%1</b>: No match found within bounds.').arg(tagsource.name))
                    continue

        if len(matches) == 1:
            set_status(QApplication.translate('Masstagging', '<b>%1</b>: Retrieving album.').arg(tagsource.name))
            stop, tracks, source_info = parse_single_match(matches, tagsource, 
                config[2], fields, tracks, files)
            info.update(source_info)
            if stop:
                set_status(QApplication.translate('Masstagging', '<b>%1</b>: Stopping.').arg(tagsource.name))
                break
    ret = []
    for track in tracks:
        ret.append(dict([(key, remove_dupes(value)) for 
            key, value in track.items()]))
    return files, ret

def replace(fields, info, retrieved, old_tracks):
    new_tracks = []
    for track in retrieved:
        info_copy = info.copy()
        info_copy.update(track)
        new_tracks.append(strip(info_copy, fields))
    if len(retrieved) > len(old_tracks):
        old_tracks = new_tracks[len(old_tracks):]
    [old.update(new) for old, new in zip(old_tracks, new_tracks)]
    return old_tracks

def remove_dupes(value):
    value = to_list(value)
    try:
        value = list(set(value))
    except TypeError:
        'Unhashable type like dictionary for pictures.'
    return value

def parse_single_match(matches, tagsource, operation, fields, tracks, files):
    source_info, source_tracks = tagsource.retrieve(matches[0][0])
    if source_tracks is None:
        source_tracks = [source_info.copy() for z in files]
    source_tracks = merge_tracks(matches[0][1], source_tracks)
    stop = False
    if operation == COMBINE_CONTINUE:
        tracks = combine(fields, source_info, source_tracks, tracks)
    elif operation == COMBINE_STOP:
        tracks = combine(fields, source_info, source_tracks, tracks)
        stop = True
    elif operation == REPLACE_CONTINUE:
        tracks = replace(fields, source_info, source_tracks, tracks)
    elif operation == REPLACE_STOP:
        tracks = replace(fields, source_info, source_tracks, tracks)
        stop = True
    return stop, tracks, source_info

def insert_status(msg):
    set_status(u':insert%s' % msg)

def search(tagsources, configs, audios, 
    pattern = '%dummy% - %album%/%artist% - %track% - %title%'):

    set_status('<b>Initializing...</b>')
    tag_groups = split_files(audios, pattern)

    source_names = dict([(z.name, z) for z in 
        tagsources])

    for group in tag_groups:
        album_groups = split_by_tag(group, 'album', 'artist').items()
        if len(album_groups) == 1:
            album, artists = album_groups[0]
        else:
            [tag_groups.extend(z[1].values()) for z in album_groups]
            continue
        if len(artists) == 1:
            artist = to_string(artists.keys()[0])
        else:
            artist = u'Various Artists'
        set_status(QApplication.translate('Masstagging', u'<br />Starting search for: <b>%1 - %2</b>)').arg(artist).arg(album))
        files = []
        results = []
        [files.extend(z) for z in artists.values()]
        for config in configs:
            tagsource = source_names[config[0]]
            set_status(QApplication.translate('Masstagging', u'Polling <b>%1<b>: ').arg(config[0]))
            group = split_by_tag(files, *tagsource.group_by)
            field = group.keys()[0]
            result = tagsource.search(field, group[field])
            if result:
                results.append([tagsource, result, files, config])
                if len(result) == 1:
                    insert_status(QApplication.translate('Masstagging', u'Exact match found.'))
                else:
                    insert_status(QApplication.translate('Masstagging', u'%1 albums found.').arg(unicode(len(result))))
            elif not result:
                results.append([tagsource, [], files, config])
                insert_status(QApplication.translate('Masstagging', u'No albums found'))
        yield results

def save_configs(configs, filename=CONFIG):
    cparser = PuddleConfig(filename)
    info_section = 'info'
    
    cparser.set(info_section, NAME, configs[NAME])
    for key in [ALBUM_BOUND, PATTERN, TRACK_BOUND, FIELDS, JFDI, DESC, 
        EXISTING_ONLY]:
        cparser.set(info_section, key, configs[key])
    
    cparser.set(info_section, 'numsources', len(configs[SOURCE_CONFIGS]))
    
    for num, config in enumerate(configs[SOURCE_CONFIGS]):
        section = 'config%s' % num
        cparser.set(section, 'source', config[0])
        cparser.set(section, 'no_match', config[1])
        cparser.set(section, 'single_match', config[2])
        cparser.set(section, 'fields', u','.join(config[3]))
        cparser.set(section, 'many_match', config[4])

def split_files(audios, pattern):
    dir_groups = split_by_tag(audios, '__dirpath', None)
    tag_groups = []

    for dirpath, files in dir_groups.items():
        tags = []
        for f in files:
            if pattern:
                tag = filenametotag(pattern, f[PATH], True)
                if tag:
                    tag.update(f.tags.copy())
                else:
                    tag = f.tags.copy()
            else:
                tag = f.tags.copy()
            tag['__file'] = f
            tags.append(tag)
        tag_groups.append(tags)
    return tag_groups

class ProfileEdit(QDialog):
    def __init__(self, tagsources, configs=None, parent=None):
        super(ProfileEdit, self).__init__(parent)
        
        self.setWindowTitle(QApplication.translate('Profile Editor', 'Edit Profile'))
        winsettings('profile', self)
        self._configs = []
        self.tagsources = tagsources
        
        self._name = QLineEdit(QApplication.translate('Profile Editor','Masstagging Profile'))
        namelayout = QHBoxLayout()
        namelabel = QLabel(QApplication.translate('Profile Editor', '&Name:'))
        namelabel.setBuddy(self._name)
        namelayout.addWidget(namelabel)
        namelayout.addWidget(self._name)
        
        self._desc = QLineEdit()
        desclabel = QLabel(QApplication.translate('Profile Editor', '&Description'))
        desclabel.setBuddy(self._desc)
        desclayout = QHBoxLayout()
        desclayout.addWidget(desclabel)
        desclayout.addWidget(self._desc, 1)
        
        self.listbox = ListBox()

        self.okcancel = OKCancel()
        self.okcancel.ok.setDefault(True)
        self.grid = QGridLayout()

        self.buttonlist = ListButtons()
        
        self.pattern = QLineEdit('%artist% - %album%/%track% - %title%')
        self.pattern.setToolTip(QApplication.translate('Profile Editor', "<p>If no tag information is found in a file, the tags retrieved using this pattern will be used instead.</p>"))
        
        self.albumBound = QSpinBox()
        self.albumBound.setToolTip(QApplication.translate('Profile Editor',"<p>The artist and album fields will be used in determining whether an album matches the retrieved one. Each field will be compared using a fuzzy matching algorithm. If the resulting average match percentage is greater or equal than what you specify here it'll be considered to match.</p>"))
        self.albumBound.setRange(0,100)
        self.albumBound.setValue(70)
        
        self.matchFields = QLineEdit('artist, title')
        self.matchFields.setToolTip(QApplication.translate('Profile Editor','<p>The fields listed here will be used in determining whether a track matches the retrieved track. Each field will be compared using a fuzzy matching algorithm. If the resulting average match percentage is greater than the "Minimum Percentage" it\'ll be considered to match.</p>'))
        self.trackBound = QSpinBox()
        self.trackBound.setRange(0,100)
        self.trackBound.setValue(80)
        
        self.jfdi = QCheckBox(QApplication.translate('Profile Editor','Brute force unmatched files.'))
        self.jfdi.setToolTip(QApplication.translate('Profile Editor',"<p>If a proper match isn't found for a file, the files will get sorted by filename, the retrieved tag sources by filename and corresponding (unmatched) tracks will matched.</p>"))
        
        self.existing = QCheckBox(QApplication.translate('Profile Editor','Update empty fields only.'))
        
        self.grid.addLayout(namelayout, 0, 0, 1, 2)
        self.grid.addLayout(desclayout, 1, 0, 1, 2)
        self.grid.addWidget(self.listbox, 2, 0)
        self.grid.setRowStretch(2, 1)
        self.grid.addLayout(self.buttonlist, 2, 1)
        self.grid.addLayout(create_buddy(QApplication.translate('Profile Editor', 'Pattern to match filenames against.'),
            self.pattern, QVBoxLayout()), 3, 0, 1, 2)
        self.grid.addLayout(create_buddy(QApplication.translate('Profile Editor', 'Minimum percentage required for album matches.'),
            self.albumBound), 4, 0, 1, 2)
        self.grid.addLayout(create_buddy(QApplication.translate('Profile Editor', 'Match tracks using fields: '),
            self.matchFields, QVBoxLayout()), 5, 0, 1, 2)
        self.grid.addLayout(create_buddy(QApplication.translate('Profile Editor','Minimum percentage required for track match.'), self.trackBound), 6, 0, 1, 2)
        self.grid.addWidget(self.jfdi, 7, 0, 1, 2)
        self.grid.addWidget(self.existing, 8, 0, 1, 2)
        self.grid.addLayout(self.okcancel, 9, 0, 1, 2)
        
        self.setLayout(self.grid)
        
        connect = lambda control, signal, slot: self.connect(
            control, SIGNAL(signal), slot)

        connect(self.okcancel, "ok" , self.okClicked)
        connect(self.okcancel, "cancel",self.close)
        connect(self.buttonlist, "add", self.addClicked)
        connect(self.buttonlist, "edit", self.editClicked)
        connect(self.buttonlist, "moveup", self.moveUp)
        connect(self.buttonlist, "movedown", self.moveDown)
        connect(self.buttonlist, "remove", self.remove)
        connect(self.buttonlist, "duplicate", self.dupClicked)
        connect(self.listbox, "itemDoubleClicked (QListWidgetItem *)",
            self.editClicked)
        connect(self.listbox, "currentRowChanged(int)", self.enableListButtons)

        if configs is not None:
            self.setConfigs(configs)
        self.enableListButtons(self.listbox.currentRow())

    def addClicked(self):
        win = ConfigEdit(self.tagsources, None, self)
        win.setModal(True)
        self.connect(win, SIGNAL('sourceChanged'), self._addSource)
        win.show()
    
    def _addSource(self, *source):
        row = self.listbox.count()
        self.listbox.addItem(config_str(source))
        self._configs.append(source)
    
    def dupClicked(self):
        row = self.listbox.currentRow()
        if row == -1:
            return
        win = ConfigEdit(self.tagsources, self._configs[row], self)
        win.setModal(True)
        self.connect(win, SIGNAL('sourceChanged'), self._addSource)
        win.show()
    
    def editClicked(self, item=None):
        if item:
            row = self.listbox.row(item)
        else:
            row = self.listbox.currentRow()
        
        if row == -1:
            return
        win = ConfigEdit(self.tagsources, self._configs[row], self)
        win.setModal(True)
        self.connect(win, SIGNAL('sourceChanged'), 
            partial(self._editSource, row))
        win.show()
    
    def _editSource(self, row, *source):
        self._configs[row] = source
        self.listbox.item(row).setText(config_str(source))
    
    def enableListButtons(self, val):
        if val == -1:
            [button.setEnabled(False) for button in self.buttonlist.widgets[1:]]
        else:
            [button.setEnabled(True) for button in self.buttonlist.widgets[1:]]
    
    def moveDown(self):
        self.listbox.moveDown(self._configs)
    
    def moveUp(self):
        self.listbox.moveUp(self._configs)
    
    def okClicked(self):
        fields = [z.strip() for z in 
            unicode(self.matchFields.text()).split(',')]
        configs = {
            SOURCE_CONFIGS: self._configs,
            PATTERN: unicode(self.pattern.text()),
            ALBUM_BOUND: self.albumBound.value(),
            TRACK_BOUND: self.trackBound.value(),
            FIELDS: fields,
            JFDI: True if self.jfdi.checkState() == Qt.Checked else False,
            NAME: unicode(self._name.text()),
            DESC: unicode(self._desc.text()),
            EXISTING_ONLY: True if self.existing.checkState() == Qt.Checked \
                else False}
        self.emit(SIGNAL('profileChanged'), configs)
        self.close()
    
    def remove(self):
        row = self.listbox.currentRow()
        if row == -1:
            return
        del(self._configs[row])
        self.listbox.takeItem(row)
    
    def setConfigs(self, configs):
        self._configs = configs[SOURCE_CONFIGS]
        [self.listbox.addItem(config_str(config)) for config 
            in self._configs]
        self.albumBound.setValue(configs[ALBUM_BOUND])
        self.pattern.setText(configs[PATTERN])
        self.matchFields.setText(u', '.join(configs[FIELDS]))
        self.trackBound.setValue(configs[TRACK_BOUND])
        self.jfdi.setCheckState(Qt.Checked if configs[JFDI] else
            Qt.Unchecked)
        self._name.setText(configs[NAME])
        self._desc.setText(configs[DESC])
        self.existing.setCheckState(Qt.Checked if 
            configs[EXISTING_ONLY] else Qt.Unchecked)

class ConfigEdit(QDialog):
    def __init__(self, tagsources, previous=None, parent=None):
        super(ConfigEdit, self).__init__(parent)
        self.setWindowTitle(QApplication.translate('Profile Editor', 'Edit Config'))
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        self._source = QComboBox()
        self._source.addItems([source.name for source in tagsources])
        layout.addLayout(create_buddy(QApplication.translate('Profile Editor', '&Source'), self._source))
        
        self._no_match = QComboBox()
        self._no_match.setToolTip(QApplication.translate('Profile Editor', '<b>Continue</b>: The lookup for the current album continue unabated if no results were returned for this Tag Source.<br /><b>Stop:</b> The lookup the current album will stop and any previous results will be used.'))
        self._no_match.addItems(NO_MATCH_OPTIONS)
        layout.addLayout(create_buddy(QApplication.translate('Profile Editor', '&If no results found: '), self._no_match))
        
        self._single_match = QComboBox()
        self._single_match.setToolTip(QApplication.translate('Profile Editor','Say FreeDB returned the following <b>artist=Linkin, album=Meteora, genre=Rock</b> and MusicBrainz <b>artist=Linkin Park, album=Hybrid Theory, title=In The End, genre=Rap</b>. <br /><br /><b>Combining</b> them means that fields with differing values will be combined, in this case "genre". The resulting tag will be <b>artist=Linkin Park, album=Hybrid Theory, title=In The End, genre=Rock, Rap</b> (ie. genre will have two values, Rock and Rap. Not the singular "Rock, Rap".)<br /><br />Choosing to <b>replace</b> fields will result in the following tag <b>artist=Linkin Park, album=Hybrid Theory, title=In The End, genre=Rap</b> (since Musicbrainz was last, it\'s genre field takes precedence)'))
        self._single_match.addItems(SINGLE_MATCH_OPTIONS)
        layout.addLayout(create_buddy(QApplication.translate('Profile Editor', '&If single match found: '), 
            self._single_match))
        
        self._fields = QLineEdit()
        tooltip = QApplication.translate('Profile Editor','Enter a comma seperated list of fields to write. <br /><br />Eg. <b>artist, album, title</b> will only write the artist, album and title fields of the retrieved tags. <br /><br />If you want to exclude some fields, but write all others start the list the tilde (~) character. Eg <b>~composer,__image</b> will write all fields but the composer and __image fields.')
        self._fields.setToolTip(tooltip)
        layout.addLayout(create_buddy('Fields:', self._fields))
        
        self._many_match = QComboBox()
        self._many_match.setToolTip(QApplication.translate('Profile Editor', "Choose the course of action if an exact match wasn't found. See the tooltip in the previous dialog for an explanation of <b>Use best match</b>."))
        self._many_match.addItems(AMBIGIOUS_MATCH_OPTIONS)
        layout.addLayout(create_buddy(QApplication.translate('Profile Editor', '&If ambiguous matches found: '), 
            self._many_match))
        
        okcancel = OKCancel()
        self.connect(okcancel, SIGNAL('ok'), self._okClicked)
        self.connect(okcancel, SIGNAL('cancel'), self.close)
        layout.addLayout(okcancel)
        
        layout.addStretch()
        
        if previous:
            self._setConfig(*previous)
    
    def _okClicked(self):
        source = unicode(self._source.currentText())
        no_match = self._no_match.currentIndex()
        single_match = self._single_match.currentIndex()
        many_matches = self._many_match.currentIndex()
        fields = fields_from_text(unicode(self._fields.text()))
        #print (source, no_match, single_match, fields, many_matches)
        self.close()
        self.emit(SIGNAL('sourceChanged'), source, no_match, single_match, 
            fields, many_matches)
    
    def _setConfig(self, source_name, no_match, single, fields, many):
        source_index = self._source.findText(source_name)
        if source_index != -1:
            self._source.setCurrentIndex(source_index)
        self._no_match.setCurrentIndex(no_match)
        self._single_match.setCurrentIndex(single)
        self._fields.setText(u', '.join(fields))
        self._many_match.setCurrentIndex(no_match)

class Config(QDialog):
    def __init__(self, tagsources, profiles=None, parent = None):
        super(Config, self).__init__(parent)
        
        self.setWindowTitle(QApplication.translate('Profile Config', 'Configure Mass Tagging'))
        winsettings('masstagging', self)
        
        self.listbox = ListBox()
        self.tagsources = tagsources

        okcancel = OKCancel()
        okcancel.ok.setDefault(True)

        self.buttonlist = ListButtons()
        
        connect = lambda control, signal, slot: self.connect(
            control, SIGNAL(signal), slot)

        connect(okcancel, "ok" , self.okClicked)
        connect(okcancel, "cancel",self.close)
        connect(self.buttonlist, "add", self.addClicked)
        connect(self.buttonlist, "edit", self.editClicked)
        connect(self.buttonlist, "moveup", self.moveUp)
        connect(self.buttonlist, "movedown", self.moveDown)
        connect(self.buttonlist, "remove", self.remove)
        connect(self.buttonlist, "duplicate", self.dupClicked)
        connect(self.listbox, "itemDoubleClicked (QListWidgetItem *)",
            self.editClicked)
        connect(self.listbox, "currentRowChanged(int)", self.enableListButtons)

        self.enableListButtons(self.listbox.currentRow())
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        layout.addWidget(QLabel(QApplication.translate('Profile Config', 'Profiles')))
        
        list_layout = QHBoxLayout()
        list_layout.addWidget(self.listbox, 1)
        list_layout.addLayout(self.buttonlist)
        
        layout.addLayout(list_layout)
        layout.addLayout(okcancel)
        if profiles is not None:
            self.setProfiles(profiles)
    
    def addClicked(self):
        win = ProfileEdit(self.tagsources, parent=self)
        win.setModal(True)
        self.connect(win, SIGNAL('profileChanged'), self._addProfile)
        win.show()
    
    def _addProfile(self, config):
        row = self.listbox.count()
        self.listbox.addItem(config[NAME])
        self._profiles.append(config)
    
    def dupClicked(self):
        row = self.listbox.currentRow()
        if row == -1:
            return
        win = ProfileEdit(self.tagsources, deepcopy(self._profiles[row]), self)
        win.setModal(True)
        self.connect(win, SIGNAL('profileChanged'), self._addProfile)
        win.show()
    
    def editClicked(self, item=None):
        if item:
            row = self.listbox.row(item)
        else:
            row = self.listbox.currentRow()
        
        if row == -1:
            return
        win = ProfileEdit(self.tagsources, self._profiles[row], self)
        win.setModal(True)
        self.connect(win, SIGNAL('profileChanged'), 
            partial(self._editProfile, row))
        win.show()
    
    def _editProfile(self, row, profile):
        self._profiles[row] = profile
        self.listbox.item(row).setText(profile[NAME])
    
    def enableListButtons(self, val):
        if val == -1:
            [button.setEnabled(False) for button in self.buttonlist.widgets[1:]]
        else:
            [button.setEnabled(True) for button in self.buttonlist.widgets[1:]]
    
    def moveDown(self):
        self.listbox.moveDown(self._profiles)
    
    def moveUp(self):
        self.listbox.moveUp(self._profiles)
    
    def okClicked(self):
        self.emit(SIGNAL('profilesChanged'), self._profiles)
        self.saveProfiles(os.path.join(SAVEDIR, 'masstagging'), self._profiles)
        self.close()
    
    def remove(self):
        row = self.listbox.currentRow()
        if row == -1:
            return
        del(self._profiles[row])
        self.listbox.takeItem(row)
    
    def loadProfiles(self, dirpath = None):
        self._profiles = []
        if not dirpath:
            dirpath = os.path.join(SAVEDIR, u'masstagging')
        for conf in glob.glob(dirpath + u'/*.conf'):
            self._profiles.append(load_config(conf))
        for profile in self._profiles:
            self.listbox.addItem(profile[NAME])
    
    def setProfiles(self, profiles):
        self._profiles = profiles
        for profile in self._profiles:
            self.listbox.addItem(profile[NAME])
    
    def saveProfiles(self, dirpath, profiles):
        filenames = {}
        order = []
        for profile in profiles:
            filename = profile[NAME] + u'.conf'
            i = 0
            while filename in filenames:
                filename = u'%s_%d%s' (profile[NAME], i, u'.conf')
                i += 1
            filenames[filename] = profile
            order.append(profile[NAME])
        files = glob.glob(os.path.join(dirpath, '*.conf'))
        for f in files:
            if f not in filenames:
                try:
                    os.remove(f)
                except EnvironmentError:
                    pass
        for filename, profile in filenames.items():
            save_configs(profile, os.path.join(dirpath, filename))
        f = open(os.path.join(dirpath, 'order'), 'w')
        f.write(u'\n'.join(order))
        f.close()

class Retriever(QWidget):
    def __init__(self, parent=None, status=None):
        super(Retriever, self).__init__(parent)
        self.receives = []
        self.emits = ['setpreview', 'clearpreview', 'enable_preview_mode',
            'writepreview', 'disable_preview_mode']
        self.wasCanceled = False
        
        self.setWindowTitle(QApplication.translate('Masstagging', 'Mass Tagging'))
        winsettings('masstaglog', self)
        self._startButton = QPushButton(QApplication.translate('Masstagging', '&Start'))
        configure = QPushButton(QApplication.translate('Masstagging', '&Configure'))
        write = QPushButton(QApplication.translate('Masstagging', '&Write'))
        clear = QPushButton(QApplication.translate('Masstagging', 'Clear &Preview'))
        self._log = QTextEdit()
        self.tagsources = status['initialized_tagsources']
        
        self._curProfile = QComboBox()

        self._status = status

        self.connect(status_obj, SIGNAL('statusChanged'), self._appendLog)
        self.connect(status_obj, SIGNAL('logappend'), self._appendLog)
        self.connect(self._curProfile, SIGNAL('currentIndexChanged(int)'),
            self.changeProfile)
        self.connect(self._startButton, SIGNAL('clicked()'), self.lookup)
        self.connect(configure, SIGNAL('clicked()'), self.configure)
        self.connect(write, SIGNAL('clicked()'), self.writePreview)
        self.connect(clear, SIGNAL('clicked()'), self.clearPreview)
        
        buttons = QHBoxLayout()
        buttons.addWidget(self._startButton)
        buttons.addWidget(configure)
        buttons.addStretch()
        buttons.addWidget(write)
        buttons.addWidget(clear)
        
        combo = QHBoxLayout()
        label = QLabel(QApplication.translate('Masstagging', '&Profile:'))
        label.setBuddy(self._curProfile)
        combo.addWidget(label)
        combo.addWidget(self._curProfile, 1)
        
        layout = QVBoxLayout()
        layout.addLayout(buttons)
        layout.addLayout(combo)
        
        layout.addWidget(self._log)
        self.setLayout(layout)
    
    def _appendLog(self, text):
        mutex.lock()
        if not isinstance(text, unicode):
            text = unicode(text, 'utf8', 'replace')
        if text.startswith(u':insert'):
            text = text[len(u':insert'):]
            self._log.textCursor().setPosition(len(self._log.toPlainText()))
            self._log.insertHtml(text)
        else:
            self._log.append(text)
        mutex.unlock()
    
    def changeProfile(self, index):
        self._configs = self._profiles[index]
    
    def clearPreview(self):
        self.emit(SIGNAL('disable_preview_mode'))
    
    def configure(self):
        win = Config(self.tagsources, self._profiles, self)
        win.setModal(True)
        self.connect(win, SIGNAL('profilesChanged'), self.setProfiles)
        win.show()
    
    def lookup(self):
        button = self.sender()
        if self._startButton.text() != QApplication.translate('Masstagging', '&Stop'):
            self.wasCanceled = False
            self._log.clear()
            self._startButton.setText(QApplication.translate('Masstagging', '&Stop'))
            self._start()
        else:
            self._startButton.setText(QApplication.translate('Masstagging', '&Start'))
            self.wasCanceled = True
    
    def loadSettings(self):
        if not os.path.exists(PROFILEDIR):
            os.mkdir(PROFILEDIR)
        self.setProfiles(load_profiles(PROFILEDIR))
    
    def _setConfigs(self, configs):
        self._configs = configs
    
    def setProfiles(self, profiles):
        self._profiles = profiles
        if not profiles:
            self._curProfile.clear()
            return
        self.disconnect(self._curProfile, SIGNAL('currentIndexChanged(int)'),
            self.changeProfile)
        old = self._curProfile.currentText()
        self._curProfile.clear()
        self._curProfile.addItems([profile[NAME] for profile in profiles])
        index = self._curProfile.findText(old)
        if index == -1:
            index = 0
        self._curProfile.setCurrentIndex(index)
        self._configs = profiles[index]
        
        if profiles[index].get(DESC):
            self._curProfile.setToolTip(profiles[index][DESC])
        self.connect(self._curProfile, SIGNAL('currentIndexChanged(int)'),
            self.changeProfile)
    
    def _start(self):
        files = self._status['selectedfiles']
        source_configs = self._configs[SOURCE_CONFIGS]
        pattern = self._configs[PATTERN]
        album_bound = self._configs[ALBUM_BOUND] / 100.0
        track_bound = self._configs[TRACK_BOUND] / 100.0
        track_fields = self._configs[FIELDS]
        jfdi = self._configs[JFDI]
        existing = self._configs[EXISTING_ONLY]
        self.emit(SIGNAL('enable_preview_mode'))
        def method():
            try:
                results = search(self.tagsources, source_configs, 
                    files, pattern)
                for result in results:
                    if not self.wasCanceled:
                        try:
                            retrieved = retrieve(result, album_bound)
                            matched = match_files(retrieved[0], retrieved[1], 
                                track_bound, track_fields, jfdi, existing)
                            thread.emit(SIGNAL('setpreview'), matched)
                        except RetrievalError, e:
                            self._appendLog(QApplication.translate('Masstagging', '<b>Error: %1</b>').arg(unicode(e)))
            except RetrievalError, e:
                self._appendLog(QApplication.translate('Masstagging', '<b>Error: %1</b>').arg(unicode(e)))
                self._appendLog(QApplication.translate('Masstagging', '<b>Stopping</b>'))
        
        def finished(value):
            self._appendLog(QApplication.translate('Masstagging', '<b>Lookup completed.</b>'))
            self._startButton.setText(QApplication.translate('Masstagging', '&Start'))
            self.wasCanceled = False
        
        thread = PuddleThread(method, self)
        self.connect(thread, SIGNAL('setpreview'), SIGNAL('setpreview'))
        self.connect(thread, SIGNAL('threadfinished'), finished)

        thread.start()
    
    def writePreview(self):
        self.emit(SIGNAL('writepreview'))
        

control = (unicode(QApplication.translate('Masstagging', 'Mass Tagging')), Retriever, RIGHTDOCK, False)

if __name__ == '__main__':
    from puddlestuff import audioinfo

    tagsources = [z[0]() for z in puddlestuff.tagsources.tagsources]
    tagsources.extend([z.info[0]() for z in 
        [exampletagsource, qltagsource]])
    status = {}
    status['initialized_tagsources'] = tagsources
    status['selectedfiles'] = []
    
    app = QApplication([])
    #win = Config(tagsources)
    #win.loadProfiles()
    #win = ConfigEdit(tagsources, previous)
    #win = MassTagging()
    win = Retriever(status = status)
    win.loadProfiles()
    win.show()
    app.exec_()