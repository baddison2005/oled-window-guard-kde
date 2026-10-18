import json
import unittest
from oled_window_guard.preferences import defaults
from oled_window_guard.display_identity import rebind_displays


def identity(port, serial='unique-LG', brand='LG', model='OLED'):
    return json.dumps([port, brand, model, serial])


class DisplayIdentityTests(unittest.TestCase):
    def test_unique_monitor_retains_both_selections_and_overrides_across_ports(self):
        a,b=identity('DP-1'),identity('HDMI-A-1')
        v=defaults();v.update(movementDisplays=[a],dimmingDisplays=[a],movementOverrides={a:{'mode':'swap'}},dimmingOverrides={a:{'windowDimDelay':17}})
        result,mapping=rebind_displays(v,[{'id':b}])
        self.assertEqual(mapping,{a:b})
        for key in ['movementDisplays','dimmingDisplays']:self.assertEqual(result[key],[b]);self.assertEqual(v[key],[a])
        self.assertEqual(result['movementOverrides'],{b:{'mode':'swap'}})
        self.assertEqual(result['dimmingOverrides'],{b:{'windowDimDelay':17}})
        self.assertEqual(rebind_displays(result,[{'id':b}]),(result,{}))

    def test_same_port_different_monitor_is_never_selected(self):
        a,b=identity('HDMI-A-1','LG'),identity('HDMI-A-1','Wacom')
        v=defaults();v['movementDisplays']=[a]
        self.assertEqual(rebind_displays(v,[{'id':b}]),(v,{}))

    def test_ambiguous_missing_or_placeholder_serial_is_not_migrated(self):
        for serial in ('','0','unknown','none','n/a','serial'):
            with self.subTest(serial=serial):
                a,b,c=identity('DP-1',serial),identity('HDMI-1',serial),identity('HDMI-2',serial)
                v=defaults();v['movementDisplays']=[a]
                outputs=[{'id':b}]+([{'id':c}] if serial=='serial' else [])
                self.assertEqual(rebind_displays(v,outputs),(v,{}))
        v=defaults();v['movementDisplays']=[identity('old1'),identity('old2')]
        self.assertEqual(rebind_displays(v,[{'id':identity('new')}]),(v,{}))

    def test_conflicting_saved_overrides_are_preserved_for_review(self):
        a,b=identity('DP-1'),identity('HDMI-1')
        v=defaults();v.update(movementDisplays=[a],movementOverrides={a:{'mode':'swap'},b:{'mode':'drift'}})
        self.assertEqual(rebind_displays(v,[{'id':b}]),(v,{}))
