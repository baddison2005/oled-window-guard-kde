"""Disposable GTK 3/4 Wayland windows; never arms OWG or assigns compositor frames."""
import argparse
import json
import os
from pathlib import Path
import time

APP_ID='org.oledwindowguard.gtkfixture'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--gtk-version',choices=('3','4'),default='3')
    args=parser.parse_args()
    os.environ['GDK_BACKEND']='wayland'
    import gi
    gi.require_version('Gtk',args.gtk_version+'.0')
    from gi.repository import GLib
    GLib.set_prgname(APP_ID)
    from gi.repository import Gtk,Gdk
    app=Gtk.Application(application_id=APP_ID)
    windows=[];previous=None;stream=None
    def sample():
        nonlocal previous
        if stream is None:return True
        display=Gdk.Display.get_default()
        rows=[]
        for window in windows:
            native=window.get_surface() if args.gtk_version=='4' else window.get_window()
            monitor=(display.get_monitor_at_surface(native) if args.gtk_version=='4' else display.get_monitor_at_window(native)) if native else None
            geometry=monitor.get_geometry() if monitor else None
            width,height=(window.get_width(),window.get_height()) if args.gtk_version=='4' else window.get_size()
            rows.append(dict(title=window.get_title(),visible=window.get_visible() and window.get_mapped(),
                clientSize=[width,height],monitorGeometry=[geometry.x,geometry.y,geometry.width,geometry.height] if geometry else None))
        if rows!=previous:
            stream.write(json.dumps(dict(monotonic=time.monotonic(),pid=os.getpid(),appId=APP_ID,
                toolkit='GTK'+args.gtk_version,platform='wayland',displayType=display.__gtype__.name,windows=rows))+'\n');stream.flush()
            previous=rows
        return True
    def activate(application):
        nonlocal stream
        if windows:
            windows[0].present();return
        display=Gdk.Display.get_default()
        if not display or 'Wayland' not in display.__gtype__.name:
            raise RuntimeError('GTK fixtures require a Wayland display')
        args.receipt.parent.mkdir(parents=True,exist_ok=True)
        stream=args.receipt.open('x')
        for number in range(1,5):
            window=Gtk.ApplicationWindow(application=application)
            window.set_title('OWG GTK'+args.gtk_version+' disposable '+str(number))
            window.set_default_size(420,180)
            label=Gtk.Label(label='Disposable GTK Wayland Move / Restore target.\n'
                'Place TWO of these windows on EACH monitor,\n'
                'on your empty test desktops, visible and uncovered.')
            label.set_margin_start(16);label.set_margin_end(16)
            label.set_margin_top(16);label.set_margin_bottom(16)
            label.set_wrap(True)
            if args.gtk_version=='4':window.set_child(label)
            else:window.add(label)
            windows.append(window)
            if args.gtk_version=='4':window.present()
            else:window.show_all()
        GLib.timeout_add(100,sample);sample()
    app.connect('activate',activate)
    try:return app.run([])
    finally:
        if stream:sample();stream.close()


if __name__=='__main__':raise SystemExit(main())
