#!/usr/bin/env python3
"""Fresh-process KDE theme/render checks without a compositor or live session bus."""
import argparse,configparser,json,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def probe(folder,app_root):
    sys.path.insert(0,str(app_root))
    from PySide6.QtGui import QPalette
    from oled_window_guard.dashboard import create_application,Dashboard,ControllerIntentClient
    from oled_window_guard.autostart import LoginStartup
    before=os.environ.get('QT_QPA_PLATFORMTHEME')
    app=create_application([])
    class Layouts:
        def read_snapshot(self):return dict(status='missing',diagnostics=[],groups=[],layouts=[])
    intents=[]
    window=Dashboard(ControllerIntentClient(lambda a,p:intents.append((a,p))),
        settings_path=folder/'config/oled-window-guard/settings.json',layout_provider=Layouts(),
        app_ids=[],login_startup=LoginStartup(folder/'config'))
    window.resize(1080,900);window.show()
    roles=(QPalette.ColorRole.Window,QPalette.ColorRole.WindowText,QPalette.ColorRole.Base,QPalette.ColorRole.Text,QPalette.ColorRole.Button)
    palette={role.name:app.palette().color(role).name() for role in roles}
    for index in range(window.tabs.count()):
        window.navigation.setCurrentRow(index)
        for _ in range(3):app.processEvents()
        assert window.grab().save(str(folder/f'page-{index}.png'))
    window.dock_menu.stop();window._read_executor.shutdown(wait=True,cancel_futures=True)
    assert intents==[],intents
    print(json.dumps(dict(beforeTheme=before,theme=os.environ.get('QT_QPA_PLATFORMTHEME'),palette=palette,
        style=app.style().objectName(),pages=window.tabs.count(),noControllerIntents=True)))
    window.hide();window.deleteLater();app.processEvents()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--app-root',type=Path,default=ROOT)
    parser.add_argument('--probe',type=Path);args=parser.parse_args()
    if args.probe:return probe(args.probe,args.app_root)
    root=Path(tempfile.mkdtemp(prefix='owg-theme-render-'));results=[]
    cases=[('dark','BreezeDark',None),('light','BreezeLight',None),('explicit-override','BreezeDark','xdgdesktopportal')]
    for name,scheme,override in cases:
        folder=root/name;folder.mkdir();env=os.environ.copy()
        for key in ('XDG_CURRENT_DESKTOP','KDE_FULL_SESSION','KDE_SESSION_VERSION','QT_STYLE_OVERRIDE','QT_QPA_PLATFORMTHEME','DISPLAY','WAYLAND_DISPLAY','PYTHONPATH'):
            env.pop(key,None)
        for key,directory in [('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_RUNTIME_DIR','runtime')]:
            path=folder/directory;path.mkdir(mode=0o700);env[key]=str(path)
        colors=Path('/usr/share/color-schemes')/(scheme+'.colors')
        (folder/'config/kdeglobals').write_bytes(colors.read_bytes())
        config=configparser.ConfigParser(interpolation=None);config.read(colors)
        def color(group,key):return '#'+''.join(f'{int(x):02x}' for x in config[group][key].split(',')[:3])
        expected=dict(Window=color('Colors:Window','BackgroundNormal'),WindowText=color('Colors:Window','ForegroundNormal'),
            Base=color('Colors:View','BackgroundNormal'),Text=color('Colors:View','ForegroundNormal'),Button=color('Colors:Button','BackgroundNormal'))
        env.update(QT_QPA_PLATFORM='offscreen',DBUS_SESSION_BUS_ADDRESS='unix:path=/nonexistent-owg-theme-bus',DBUS_SYSTEM_BUS_ADDRESS='unix:path=/nonexistent-owg-system-bus',PYTHONDONTWRITEBYTECODE='1')
        if override:env['QT_QPA_PLATFORMTHEME']=override
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--probe',str(folder),'--app-root',str(args.app_root.resolve())],env=env,capture_output=True,text=True,timeout=30)
        (folder/'stdout.log').write_text(result.stdout);(folder/'stderr.log').write_text(result.stderr)
        assert result.returncode==0,(name,result.stdout,result.stderr)
        row=json.loads(result.stdout.splitlines()[-1]);assert row['noControllerIntents'] and row['pages']==6,row
        assert row['theme']==(override or 'kde'),row
        if not override:assert row['palette']==expected,(name,row,expected)
        results.append(dict(case=name,**row))
    report=dict(result='PASS',cases=results,normalDesktopUntouched=True,appRoot=str(args.app_root.resolve()))
    (root/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));print('Artifacts:',root)


if __name__=='__main__':main()
