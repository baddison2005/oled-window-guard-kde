import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oled_window_guard.adapter import AdapterLifetime, PLUGIN, installed_adapter

class AdapterLifetimeTests(unittest.TestCase):
    def test_removed_owned_adapter_pauses_before_reloading(self):
        lifetime=AdapterLifetime(); lifetime.owned=True
        events=[]
        class Interface:
            def call(self, *args):
                return False
        def pause():
            events.append('pause'); return {'ok': True}
        with patch.object(lifetime,'interface',return_value=Interface()), \
             patch.object(lifetime,'value',side_effect=lambda value:value), \
             patch.object(lifetime,'start',side_effect=lambda: events.append('reload')):
            self.assertIsNone(lifetime.recover(pause))
        self.assertEqual(events,['pause','reload'])

    def test_recovery_refuses_reload_when_pause_fails_or_probe_errors(self):
        lifetime=AdapterLifetime(); lifetime.owned=True
        class Interface:
            def call(self, *args):
                return False
        with patch.object(lifetime,'interface',return_value=Interface()), \
             patch.object(lifetime,'value',side_effect=lambda value:value), \
             patch.object(lifetime,'start') as start:
            self.assertIn('Cannot pause',lifetime.recover(lambda: {'ok':False}))
            start.assert_not_called()
        with patch.object(lifetime,'interface',return_value=Interface()), \
             patch.object(lifetime,'value',side_effect=RuntimeError('bus unavailable')), \
             patch.object(lifetime,'start') as start:
            self.assertEqual(lifetime.recover(lambda: self.fail('unexpected pause')),'bus unavailable')
            start.assert_not_called()

    def test_present_or_unowned_adapter_is_not_recovered(self):
        lifetime=AdapterLifetime()
        with patch.object(lifetime,'interface') as interface:
            self.assertIsNone(lifetime.recover(lambda: self.fail('unexpected pause')))
            interface.assert_not_called()
        lifetime.owned=True
        with patch.object(lifetime,'interface'), patch.object(lifetime,'value',return_value=True):
            self.assertIsNone(lifetime.recover(lambda: self.fail('unexpected pause')))

    def test_only_complete_installed_package_is_discovered(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'XDG_DATA_HOME': folder, 'XDG_DATA_DIRS': folder+'/empty'}):
            self.assertIsNone(installed_adapter())
            package=Path(folder)/'kwin/scripts'/PLUGIN
            (package/'contents/ui').mkdir(parents=True)
            (package/'metadata.json').write_text(json.dumps(dict(KPlugin=dict(Id=PLUGIN), **{'X-Plasma-MainScript':'ui/main.qml'})))
            (package/'contents/ui/main.qml').write_text('')
            self.assertIsNone(installed_adapter())
            (package/'contents/ui/planning_worker.js').write_text('')
            self.assertEqual(installed_adapter(), package/'contents/ui/main.qml')

    def test_existing_adapter_is_replaced_owned_and_unloaded_on_quit(self):
        calls=[]
        class Interface:
            def call(self, method, *args):
                calls.append((method,args))
                if method=='isScriptLoaded': return sum(m=='isScriptLoaded' for m,a in calls)==1
                if method=='unloadScript': return True
                if method=='loadDeclarativeScript': return 9
                from types import SimpleNamespace
                return SimpleNamespace(type=lambda:None)
        lifetime=AdapterLifetime()
        with patch('oled_window_guard.adapter.installed_adapter', return_value=Path('/tmp/main.qml')), \
             patch.object(lifetime, 'interface', return_value=Interface()), \
             patch.object(lifetime, 'value', side_effect=lambda value:value):
            self.assertIsNone(lifetime.start())
            self.assertTrue(lifetime.owned)
            lifetime.stop()
        self.assertEqual([method for method,args in calls],
            ['isScriptLoaded','unloadScript','isScriptLoaded','loadDeclarativeScript','run','unloadScript'])
        self.assertFalse(lifetime.owned)

    def test_stale_adapter_removal_failure_never_loads_a_second_observer(self):
        for refused in (True,False):
            calls=[]
            class Interface:
                def call(self, method, *args):
                    calls.append(method)
                    return method=='isScriptLoaded' or not refused
            lifetime=AdapterLifetime()
            with patch('oled_window_guard.adapter.installed_adapter',return_value=Path('/tmp/main.qml')), \
                 patch.object(lifetime,'interface',return_value=Interface()), \
                 patch.object(lifetime,'value',side_effect=lambda value:value):
                self.assertIsNotNone(lifetime.start())
            self.assertNotIn('loadDeclarativeScript',calls)
            self.assertFalse(lifetime.owned)

    def test_versioned_runtime_selected_and_invalid_or_incomplete_index_refused(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'XDG_DATA_HOME':folder,'XDG_DATA_DIRS':folder+'/empty'}):
            package=Path(folder)/'kwin/scripts'/PLUGIN
            content=package/'contents';content.mkdir(parents=True)
            (package/'metadata.json').write_text(json.dumps(dict(KPlugin=dict(Id=PLUGIN), **{'X-Plasma-MainScript':'ui/main.qml'})))
            index=content/'runtime-build.json'
            index.write_text(json.dumps({'schema':1,'main':'../../outside.qml'}))
            self.assertIsNone(installed_adapter())
            main='build_'+'a'*64+'/ui/main.qml'
            index.write_text(json.dumps({'schema':1,'main':main}))
            target=content/main;target.parent.mkdir(parents=True);target.write_text('')
            self.assertIsNone(installed_adapter())
            target.with_name('planning_worker.js').write_text('')
            self.assertEqual(installed_adapter(),target)
            index.write_text('[]')
            self.assertIsNone(installed_adapter())

    def test_failed_owned_start_unloads_only_its_adapter(self):
        calls=[]
        class Interface:
            def call(self, method, *args):
                calls.append((method,args))
                return {'isScriptLoaded':False, 'loadDeclarativeScript':7}.get(method)
        lifetime=AdapterLifetime()
        with patch('oled_window_guard.adapter.installed_adapter', return_value=Path('/tmp/main.qml')), \
             patch.object(lifetime, 'interface', return_value=Interface()), \
             patch.object(lifetime, 'value', side_effect=lambda value:value):
            # Use an explicit startup failure rather than a synthetic successful
            # QDBus result; ownership still must be released.
            with patch('oled_window_guard.adapter.QDBusMessage') as message:
                message.MessageType.ErrorMessage=1
                original=Interface.call
                def call(self, method, *args):
                    if method=='run':
                        from types import SimpleNamespace
                        calls.append((method,args))
                        return SimpleNamespace(type=lambda:1)
                    return original(self,method,*args)
                with patch.object(Interface,'call',call):
                    self.assertIn('refused', lifetime.start())
            self.assertFalse(lifetime.owned)
        self.assertEqual(calls[-1], ('unloadScript',(PLUGIN,)))

if __name__=='__main__':
    unittest.main()
