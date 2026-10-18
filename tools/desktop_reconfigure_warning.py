"""Verify owned-observer recovery during a warning without changing settings."""
import time
import hashlib
from pathlib import Path
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from oled_window_guard.adapter import PLUGIN, installed_adapter
from oled_window_guard.session import SERVICE

NOT_INSTALLED_REASON='KDE reload test requires the verified teardown fix in the installed package'


def require_fixed_installation():
    main=installed_adapter()
    if main is None:raise RuntimeError(NOT_INSTALLED_REASON)
    source=Path(__file__).resolve().parents[1]/'packages/kwin-script/contents/ui'
    try:
        for name in ('main.qml','GuardedWriter.qml','OrderedGuardedWriter.qml'):
            expected=(source/name).read_bytes()
            if b'prepareShutdown' not in expected or hashlib.sha256((main.parent/name).read_bytes()).digest()!=hashlib.sha256(expected).digest():
                raise RuntimeError(NOT_INSTALLED_REASON)
    except OSError as error:
        raise RuntimeError(NOT_INSTALLED_REASON) from error


def call(path, interface, method, *args):
    proxy=QDBusInterface('org.kde.KWin',path,interface,QDBusConnection.sessionBus())
    proxy.setTimeout(1000)
    reply=proxy.call(method,*args)
    if reply.type()==QDBusMessage.MessageType.ErrorMessage:
        raise RuntimeError('KWin '+method+' failed')
    return reply.arguments()[0] if reply.arguments() else None


def inspect():
    owner=QDBusConnection.sessionBus().interface().serviceOwner(SERVICE)
    if not owner.isValid() or not owner.value():
        raise RuntimeError('Controller owner unavailable')
    loaded=call('/Scripting','org.kde.kwin.Scripting','isScriptLoaded',PLUGIN)
    if type(loaded) is not bool:raise RuntimeError('Invalid adapter load status')
    return dict(owner=owner.value(),loaded=loaded)


def reload_configuration():
    require_fixed_installation()
    call('/KWin','org.kde.KWin','reconfigure')


def reconfigure_during_warning(current,status,record,probe=inspect,reconfigure=reload_configuration,
                               clock=time.monotonic,sleep=time.sleep):
    remaining=current.get('warningRemainingSeconds',0)
    if not current.get('skipAvailable') or not current.get('transactionActive') or remaining<5:
        raise RuntimeError('KDE reload requires an active warning with at least five seconds left')
    before=probe()
    if not before['loaded']:raise RuntimeError('Observer not loaded before KDE reload')
    expected={r['id'] for r in current.get('displays',[])}
    if len(expected)!=2:raise RuntimeError('Both display identities required before KDE reload')
    record('reconfigure-request',dict(remaining=remaining,before=before))
    reconfigure()
    started=clock()
    deadline=started+15
    unloaded=False
    recovered=False
    while clock()<deadline:
        facts=probe()
        if facts['owner']!=before['owner']:raise RuntimeError('Controller restarted during KDE reload')
        unloaded |= not facts['loaded']
        fresh=status()
        if recovered or fresh.get('paused') is True:
            if any(fresh.get(k) is not v for k,v in dict(paused=True,movementReady=False,
                        transactionActive=False,restoreAvailable=False,skipAvailable=False).items()):
                raise RuntimeError('Product did not remain paused and idle after recovery')
        elif fresh.get('restoreAvailable') or fresh.get('lastResult')=='committed':
            raise RuntimeError('Movement committed during KDE reload')
        recovered |= (unloaded and facts['loaded'] and fresh.get('paused') is True
                      and fresh.get('nativeObservation',{}).get('available') is True
                      and {r['id'] for r in fresh.get('displays',[])}==expected)
        record('reconfigure-status',dict(facts=facts,status=fresh,recovered=recovered))
        # Wait past the old warning expiry as well as validating fresh facts.
        if recovered and clock()-started>=remaining+2:
            record('reconfigure-recovery-verified',dict(elapsed=clock()-started,
                   observerUnloaded=unloaded,sameController=True,oldWarningExpired=True,status=fresh))
            return fresh
        sleep(.1)
    raise RuntimeError('Observer unload and paused recovery not verified after KDE reload')
