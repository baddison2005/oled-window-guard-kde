"""Reversible, single-key live-library change for supervised acceptance."""
import json
import subprocess
from oled_window_guard.layout_provider import LayoutProvider, GROUP


class PaddingChange:
    def __init__(self,backup,record,provider=None,write=None):
        self.provider=provider or LayoutProvider()
        self.record=record
        self.write=write or self._write
        self.original=self.provider._sample()
        raw=self.original['LayoutPadding']
        if raw==self.provider._missing_token('LayoutPadding'):
            raise RuntimeError('Padding key must exist for reversible physical test')
        value=float(raw)
        if not value.is_integer() or not 0<=value<=200:
            raise RuntimeError('Expected integer padding between zero and 200')
        self.changed=str(int(value)+1 if value<200 else 199)
        self.applied=False
        with backup.open('x') as stream:
            json.dump(dict(configFile=str(self.provider.config_file),group=GROUP,key='LayoutPadding',original=raw,temporary=self.changed,originalRaw=self.original),stream,indent=2)
        self.record('library-backup',str(backup))

    def _write(self,value):
        subprocess.run(['kwriteconfig6','--file',str(self.provider.config_file),'--group',GROUP,'--key','LayoutPadding',value],check=True,timeout=5)

    def apply(self):
        if self.provider._sample()!=self.original:
            raise RuntimeError('Library changed before temporary padding edit; no edit applied')
        self.applied=True
        self.write(self.changed)
        current=self.provider.read_snapshot()
        if current.get('executionReady') is not True or current.get('revision')==self.provider._revision(self.original):
            raise RuntimeError('Temporary library revision not verified')
        self.record('library-changed',dict(key='LayoutPadding',original=self.original['LayoutPadding'],temporary=self.changed,revision=current['revision']))

    def restore(self):
        if not self.applied:return
        current=self.provider._sample()
        if current['LayoutPadding']!=self.changed:
            raise RuntimeError('Padding changed externally; refusing to overwrite it. Original value is in library backup.')
        self.write(self.original['LayoutPadding'])
        self.applied=False
        if self.provider._sample()!=self.original:
            raise RuntimeError('Padding restored but other library data changed; original revision not recovered')
        self.record('library-restored',dict(revision=self.provider._revision(self.original),padding=self.original['LayoutPadding']))
