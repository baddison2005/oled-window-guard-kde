#include <kwin/config-kwin.h>
#include <kwin/input.h>
#include <kwin/pointer_input.h>
#include <kwin/keyboard_input.h>
#include <kwin/input_event.h>
#include <kwin/input_event_spy.h>
#include <kwin/core/output.h>
#include <kwin/core/session.h>
#include <kwin/effect/effecthandler.h>
#include <kwin/main.h>
#include <kwin/workspace.h>
#include <kwin/window.h>
#include <kwin/internalwindow.h>
#include <QWindow>
#include <QCoreApplication>
#include <QDebug>
#include <QDBusConnection>
#include <QDBusMessage>
#include <QDBusPendingCallWatcher>
#include <QDBusPendingReply>
#include <QDBusServiceWatcher>
#include <QDBusVariant>
#include <QElapsedTimer>
#include <QFileInfo>
#include <QMap>
#include <QPointer>
#include <QSet>
#include <QThread>
#include <QUuid>
#include <QVariantMap>
#include <QQmlExtensionPlugin>
#include <qqml.h>
#include <memory>
#include <algorithm>
#include <cmath>

// No effect, D-Bus endpoint, event injection, device access or client writes.
// The renderer may only reorder its authenticated internal dimming plane.
// This QObject and its spy live exactly as long as the owning KWin QML script.
class NativeObserver;
class ObserverSpy final : public KWin::InputEventSpy {
public:
    explicit ObserverSpy(NativeObserver *owner) : owner(owner) {}
    void pointerMotion(KWin::PointerMotionEvent *) override;
    void pointerButton(KWin::PointerButtonEvent *) override;
    void pointerAxis(KWin::PointerAxisEvent *) override;
    void keyboardKey(KWin::KeyboardKeyEvent *) override;
#define OWG_OTHER_INPUT(method, event) void method(KWin::event *) override;
    OWG_OTHER_INPUT(touchDown, TouchDownEvent)
    OWG_OTHER_INPUT(touchMotion, TouchMotionEvent)
    OWG_OTHER_INPUT(touchUp, TouchUpEvent)
    OWG_OTHER_INPUT(pinchGestureBegin, PointerPinchGestureBeginEvent)
    OWG_OTHER_INPUT(pinchGestureUpdate, PointerPinchGestureUpdateEvent)
    OWG_OTHER_INPUT(pinchGestureEnd, PointerPinchGestureEndEvent)
    OWG_OTHER_INPUT(pinchGestureCancelled, PointerPinchGestureCancelEvent)
    OWG_OTHER_INPUT(swipeGestureBegin, PointerSwipeGestureBeginEvent)
    OWG_OTHER_INPUT(swipeGestureUpdate, PointerSwipeGestureUpdateEvent)
    OWG_OTHER_INPUT(swipeGestureEnd, PointerSwipeGestureEndEvent)
    OWG_OTHER_INPUT(swipeGestureCancelled, PointerSwipeGestureCancelEvent)
    OWG_OTHER_INPUT(holdGestureBegin, PointerHoldGestureBeginEvent)
    OWG_OTHER_INPUT(holdGestureEnd, PointerHoldGestureEndEvent)
    OWG_OTHER_INPUT(holdGestureCancelled, PointerHoldGestureCancelEvent)
    OWG_OTHER_INPUT(switchEvent, SwitchEvent)
    OWG_OTHER_INPUT(tabletToolProximityEvent, TabletToolProximityEvent)
    OWG_OTHER_INPUT(tabletToolAxisEvent, TabletToolAxisEvent)
    OWG_OTHER_INPUT(tabletToolTipEvent, TabletToolTipEvent)
    OWG_OTHER_INPUT(tabletToolButtonEvent, TabletToolButtonEvent)
    OWG_OTHER_INPUT(tabletPadButtonEvent, TabletPadButtonEvent)
    OWG_OTHER_INPUT(tabletPadStripEvent, TabletPadStripEvent)
    OWG_OTHER_INPUT(tabletPadRingEvent, TabletPadRingEvent)
    OWG_OTHER_INPUT(tabletPadDialEvent, TabletPadDialEvent)
#undef OWG_OTHER_INPUT
private:
    NativeObserver *owner;
};

class NativeObserver : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool available READ available NOTIFY availabilityChanged)
    Q_PROPERTY(QString epoch READ observerEpoch CONSTANT)
    Q_PROPERTY(double cancellationGeneration READ generation NOTIFY generationChanged)
    Q_PROPERTY(int protocol READ protocol CONSTANT)
public:
    explicit NativeObserver(QObject *parent = nullptr)
        : QObject(parent), epoch(QUuid::createUuid().toString(QUuid::WithoutBraces)) {
        // KWin private APIs are only usable in the exact compositor/Qt build.
        // Test application identity before dereferencing kwinApp's static cast.
        auto *app = QCoreApplication::instance();
        if (!app || app->applicationName() != "kwin" ||
                QFileInfo(app->applicationFilePath()).fileName() != "kwin_wayland" ||
                app->applicationVersion() != QStringLiteral(KWIN_PLUGIN_VERSION_STRING) ||
                QString::fromLatin1(qVersion()) != QStringLiteral(QT_VERSION_STR) ||
                thread() != app->thread() || QThread::currentThread() != app->thread() ||
                app->property("owgNativeObserver1").value<QObject *>()) {
            qWarning()<<"OWG_NATIVE rejected context"<<(app?app->applicationName():QString())
                <<(app?app->applicationVersion():QString())<<qVersion();
            return;
        }
        if (!KWin::workspace() || !KWin::input() || !KWin::effects) return;
        clock.start();
        // KWin's query includes both Locked and AcquiringLock, unlike a cached
        // unlocked notification. The direct hooks below cover later transitions.
        lockState = KWin::effects->isScreenLocked() ? "locked" : "unlocked";
        if (auto *session = KWin::kwinApp()->session()) {
            sessionActivity = session->isActive() ? "active" : "inactive";
            connect(session, &KWin::Session::aboutToSleep, this, [this] {
                sleepState="sleeping"; unlockInputPending=true; sessionBoundary("native-sleep");
            }, Qt::DirectConnection);
            connect(session, &KWin::Session::awoke, this, [this] {
                sleepState="awake"; sessionBoundary("native-wake");
            }, Qt::DirectConnection);
            connect(session, &KWin::Session::activeChanged, this, [this](bool active) {
                sessionActivity=active?"active":"inactive"; if(!active)unlockInputPending=true; sessionBoundary("native-session");
            }, Qt::DirectConnection);
        }
        connect(KWin::effects, &KWin::EffectsHandler::screenAboutToLock, this, [this] {
            lockState="locking"; sessionBoundary("native-lock");
        }, Qt::DirectConnection);
        connect(KWin::effects, &KWin::EffectsHandler::screenLockingChanged, this, [this](bool locked) {
            const bool wasLocked=lockState=="locked";
            lockState=locked?"locked":lockState=="locking"?"locking":"unlocked";
            if(wasLocked && !locked) unlockInputPending=true;
            sessionBoundary("native-lock");
        }, Qt::DirectConnection);
        // Session exposes sleep transitions but no initial sleep-state getter.
        // Bootstrap from logind asynchronously; never block the compositor or
        // infer wakefulness from input. Errors and owner replacement fail closed.
        auto *loginWatcher = new QDBusServiceWatcher(QStringLiteral("org.freedesktop.login1"),
            QDBusConnection::systemBus(), QDBusServiceWatcher::WatchForOwnerChange, this);
        connect(loginWatcher, &QDBusServiceWatcher::serviceOwnerChanged, this,
            [this](const QString &, const QString &, const QString &owner) {
                sleepState="unknown";sessionBoundary("native-login-service");
                if (!owner.isEmpty()) bootstrapSleepState();
            });
        bootstrapSleepState();
        connect(KWin::workspace(), &KWin::Workspace::outputsChanged, this, [this] {
            ++topologyGeneration; pruneOutputActivity(); cancel("native-topology");
        }, Qt::DirectConnection);
        connect(KWin::workspace(), &KWin::Workspace::windowActivated, this, [this](KWin::Window *) {
            const auto next=focusedOutputs();
            // New focus on a target output protects its windows immediately.
            // Leaving that output for another app/display is not interaction
            // with the target and does not invalidate its movement token.
            for (const auto &name:next) ++outputFocusCounts[name];
            ++focusGeneration; cancel("native-focus");
        }, Qt::DirectConnection);
        // Construct the private-ABI spy only after every context/version gate.
        spy=std::make_unique<ObserverSpy>(this);
        KWin::input()->installInputEventSpy(spy.get());
        installed=true;
        app->setProperty("owgNativeObserver1", QVariant::fromValue(static_cast<QObject *>(this)));
    }
    ~NativeObserver() override {
        // Remove the spy before notifying QML or releasing state. No callback
        // may target a partially destroyed controller during script teardown.
        if (installed) {
            KWin::input()->uninstallInputEventSpy(spy.get());
            spy.reset();
            installed=false;
            emit availabilityChanged();
            cancel("native-unavailable");
        }
        auto *app=QCoreApplication::instance();
        if (app && app->property("owgNativeObserver1").value<QObject *>() == this)
            app->setProperty("owgNativeObserver1", QVariant());
    }
    bool available() const { return installed && !exhausted; }
    QString observerEpoch() const { return epoch; }
    double generation() const { return double(cancellationGeneration); }
    int protocol() const { return 1; }
    // Validity of a cancellation token is NOT permission to write geometry.
    // All session, quiet-time, identity, collision and settings gates still apply.
    Q_INVOKABLE bool isCurrent(const QString &expectedEpoch, double expectedGeneration) const {
        return available() && QThread::currentThread()==thread() && expectedEpoch==epoch &&
            expectedGeneration==double(cancellationGeneration);
    }
    // After an observed lock/sleep/inactive cycle, reconcile released input directly
    // from KWin on its main thread. Startup remains unknown; held input never
    // acquires authority, and this performs no input injection or client writes.
    Q_INVOKABLE void refreshAfterUnlock() {
        if(!available() || QThread::currentThread()!=thread() || !unlockInputPending ||
                lockState!="unlocked" || sleepState!="awake" || sessionActivity!="active" ||
                KWin::effects->isScreenLocked() || !KWin::input()->pointer() || !KWin::input()->keyboard() ||
                KWin::input()->pointer()->buttons()!=Qt::NoButton || !KWin::input()->keyboard()->pressedKeys().isEmpty()) return;
        unlockInputPending=false;buttonKnown=true;buttonsDown=false;
        heldOutputs.clear();heldKeyOutputs.clear();extraKeyOutputs.clear();
        cancel("native-unlock-input");
    }
    Q_INVOKABLE QVariantMap snapshot() const {
        const auto sampledAt=clock.elapsed();
        QVariantMap result{{"schema",2},{"epoch",epoch},{"sequence",double(sequence)},
            {"topologyGeneration",double(topologyGeneration)},{"sessionGeneration",double(sessionGeneration)},
            {"focusGeneration",double(focusGeneration)},{"lockState",lockState},{"sleepState",sleepState},
            {"sessionActivity",sessionActivity},{"ageMs",lastActivityMs<0?-1:clock.elapsed()-lastActivityMs},
            {"pointer",double(pointerCount)},{"button",double(buttonCount)},{"axis",double(axisCount)},
            {"keyboard",double(keyCount)},{"unknownFocusKeyboard",double(unknownFocusKeyCount)},
            {"focusKnown",focusKnown()},{"buttonKnown",buttonKnown},{"buttonsDown",buttonsDown},
            {"cancellationGeneration",generation()},{"immediateCancellation",available()},
            {"otherInputObserved",otherInputObserved},{"interactionProtocol",1},
            {"graceProtocol",1},{"monotonicMs",sampledAt},
            {"kwinVersion",QStringLiteral(KWIN_PLUGIN_VERSION_STRING)},{"qtVersion",QStringLiteral(QT_VERSION_STR)}};
        QVariantList outputs;
        QSet<QString> names;
        const auto availableOutputs=available()?KWin::workspace()->outputs():decltype(KWin::workspace()->outputs())();
        bool complete=!availableOutputs.empty() && availableOutputs.size()<=16;
        for (auto *output:availableOutputs) {
            const auto name=output->name();
            if (name.isEmpty() || name.size()>64 || names.contains(name)) { complete=false;continue; }
            names.insert(name);
            if (outputs.size()==16) break;
            const auto last=outputLastActivity.value(name,-1);
            outputs.append(QVariantMap{{"id",name},{"events",double(outputCounts.value(name))},
                {"ageMs",last<0?-1:clock.elapsed()-last},
                {"interactionEvents",double(outputInteractionCounts.value(name))},
                {"interactionAgeMs",clock.elapsed()-outputLastInteraction.value(name,interactionEpochMs)},
                {"lastInteractionMs",outputLastInteraction.value(name,-1)},
                {"keysDown",std::any_of(heldKeyOutputs.cbegin(),heldKeyOutputs.cend(),[&name](const auto &ids){return ids.contains(name);})},
                {"focusEvents",double(outputFocusCounts.value(name))},
                {"buttonsDown",heldOutputs.contains(name)}});
        }
        result["outputs"]=outputs;result["outputIdentityComplete"]=complete;
        return result;
    }
    // Read the compositor's client type, never infer it from an application
    // name. X11 configure positions use integer logical coordinates.
    Q_INVOKABLE QVariant placementUsesIntegerPosition(const QString &identity) const {
        if (!available() || QThread::currentThread()!=thread()) return {};
        for (auto *window:KWin::workspace()->windows())
            if (window->internalId().toString()==identity)
                return window->inherits("KWin::X11Window");
        return {};
    }
    Q_INVOKABLE QVariantMap frameSizeBounds(const QString &identity) const {
        if (!available() || QThread::currentThread()!=thread()) return {};
        for (auto *window:KWin::workspace()->windows()) if (window->internalId().toString()==identity) {
            const auto minimum=window->minSize(), maximum=window->maxSize();
            const auto margins=window->frameMargins();
            return {{"minimum",QVariantMap{{"width",minimum.width()+margins.left()+margins.right()},
                {"height",minimum.height()+margins.top()+margins.bottom()}}},
                {"maximum",QVariantMap{{"width",maximum.width()+margins.left()+margins.right()},
                {"height",maximum.height()+margins.top()+margins.bottom()}}}};
        }
        return {};
    }
    Q_INVOKABLE bool frameSizeSupported(const QString &identity, double width, double height) const {
        if (!available() || QThread::currentThread()!=thread() || !std::isfinite(width) || !std::isfinite(height) || width<=0 || height<=0) return false;
        for (auto *window:KWin::workspace()->windows()) if (window->internalId().toString()==identity) {
            const auto legal=window->constrainFrameSize(QSizeF(width,height));
            return legal.width()==width && legal.height()==height;
        }
        return false;
    }
    // Only reorder our authenticated internal paint plane. Client geometry,
    // opacity, focus and stacking are never assigned here.
    Q_INVOKABLE bool isPassiveOverlay(const QString &identity) const {
        if (!available() || QThread::currentThread()!=thread()) return false;
        for (auto *window:KWin::workspace()->windows()) {
            if (window->internalId().toString()!=identity || !window->isInternal() ||
                    window->pid()!=-1 || !window->resourceClass().isEmpty()) continue;
            auto *internal=qobject_cast<KWin::InternalWindow *>(window);
            auto *handle=internal?internal->handle():nullptr;
            return handle && handle->flags().testFlag(Qt::WindowTransparentForInput) &&
                handle->flags().testFlag(Qt::WindowDoesNotAcceptFocus);
        }
        return false;
    }
    Q_INVOKABLE bool positionDimmingPlane(const QString &caption, bool wholeDisplay) {
        if (!available() || QThread::currentThread()!=thread() ||
                !caption.startsWith(QStringLiteral("OLED Window Guard dimming ")) || caption.size()>128) return false;
        KWin::Window *plane=nullptr;
        for (auto *window:KWin::workspace()->windows())
            if (window->isInternal() && window->caption()==caption && window->pid()==-1 && window->resourceClass().isEmpty()) {
                if (plane) return false;
                plane=window;
            }
        if (!plane) return false;
        for (auto *dock:KWin::workspace()->windows()) {
            const bool cairo=dock->resourceClass()==QStringLiteral("cairo-dock") && !dock->isMovable() && !dock->isResizable();
            const bool warning=dock->isInternal() && dock->caption()==QStringLiteral("OLED Window Guard warning") &&
                dock->pid()==-1 && dock->resourceClass().isEmpty();
            if ((!cairo && !warning) || !plane->frameGeometry().intersects(dock->frameGeometry())) continue;
            if (!wholeDisplay && plane->stackingOrder()>dock->stackingOrder()) KWin::workspace()->stackBelow(plane,dock);
            else if (wholeDisplay && plane->stackingOrder()<dock->stackingOrder()) KWin::workspace()->raiseWindow(plane);
        }
        return true;
    }
    enum class Kind { Pointer, Button, Axis, Key };
    void keyboardEvent(KWin::KeyboardKeyEvent *event) {
        if (!available()) return;
        extraKeyOutputs=heldKeyOutputs.value(event->nativeScanCode);
        if (event->state==KWin::KeyboardKeyState::Released) heldKeyOutputs.remove(event->nativeScanCode);
        else {
            extraKeyOutputs.unite(focusedOutputs());
            heldKeyOutputs[event->nativeScanCode]=extraKeyOutputs;
        }
        record(Kind::Key);extraKeyOutputs.clear();
    }
    void otherInput() {
        if (!available()) return;
        // These input families cancel synchronously but do not pretend to prove
        // their held-contact semantics. Keep a lifetime coverage blocker.
        otherInputObserved=true;resetActivity();cancel("native-other-input");
    }
    void record(Kind kind, const QPointF *position=nullptr, Qt::MouseButtons buttons=Qt::NoButton) {
        if (!available()) return;
        if (lockState=="locking" || lockState=="locked" || sleepState=="sleeping" || sessionActivity=="inactive") {
            cancel("native-input-blocked");return;
        }
        if (kind==Kind::Pointer) ++pointerCount;
        else if (kind==Kind::Button) {
            // A held button inherited across observer/session startup has no
            // proven origin. Keep every output blocked until full release.
            if (!buttonKnown && buttons!=Qt::NoButton)
                for (auto *output:KWin::workspace()->outputs()) heldOutputs.insert(output->name());
            ++buttonCount;buttonKnown=true;buttonsDown=buttons!=Qt::NoButton;
        }
        else if (kind==Kind::Axis) ++axisCount;
        else ++keyCount;
        ++sequence;lastActivityMs=clock.elapsed();
        QSet<QString> affected;
        if (position) {
            if (auto *output=KWin::workspace()->outputAt(*position)) {
                attribute(output->name());affected.insert(output->name());
            }
        }
        if (kind==Kind::Key) {
            auto *focused=focusKnown()?KWin::workspace()->activeWindow():nullptr;
            if (!focused) ++unknownFocusKeyCount;
            bool attributed=false;
            if (focused && focused->frameGeometry().isValid()) {
                const auto frame=focused->frameGeometry();
                for (auto *output:KWin::workspace()->outputs()) {
                    const auto rect=output->geometry();
                    if (frame.x()<rect.x()+rect.width() && rect.x()<frame.x()+frame.width() &&
                            frame.y()<rect.y()+rect.height() && rect.y()<frame.y()+frame.height()) {
                        attribute(output->name());affected.insert(output->name());attributed=true;
                    }
                }
            }
            if (!attributed) for (auto *output:KWin::workspace()->outputs()) {
                attribute(output->name());affected.insert(output->name());
            }
            for (const auto &name:extraKeyOutputs) if (!affected.contains(name)) {attribute(name);affected.insert(name);}
        }
        // Motion alone is diagnostic. Buttons, scrolling, typing and dragging
        // update interaction counters only on the displays they affect.
        const bool interaction=kind!=Kind::Pointer || buttons!=Qt::NoButton;
        if (interaction) {
            if (affected.isEmpty()) for (auto *output:KWin::workspace()->outputs()) affected.insert(output->name());
            for (const auto &name:affected) {
                ++outputInteractionCounts[name];outputLastInteraction[name]=lastActivityMs;
            }
        }
        if (kind==Kind::Button || (kind==Kind::Pointer && buttons!=Qt::NoButton)) {
            if (buttons==Qt::NoButton) heldOutputs.clear();
            else heldOutputs.unite(affected);
        }
        // All fields and the native token are updated before direct QML delivery.
        cancel("native-input");
    }
Q_SIGNALS:
    void invalidated(const QString &reason);
    void generationChanged();
    void availabilityChanged();
private:
    void bootstrapSleepState() {
        const auto boundary=sessionGeneration;
        const auto request=++sleepRequest;
        auto message=QDBusMessage::createMethodCall(QStringLiteral("org.freedesktop.login1"),
            QStringLiteral("/org/freedesktop/login1"),QStringLiteral("org.freedesktop.DBus.Properties"),
            QStringLiteral("Get"));
        message.setArguments({QStringLiteral("org.freedesktop.login1.Manager"),QStringLiteral("PreparingForSleep")});
        auto *watcher=new QDBusPendingCallWatcher(QDBusConnection::systemBus().asyncCall(message,2000),this);
        connect(watcher,&QDBusPendingCallWatcher::finished,this,[this,boundary,request](QDBusPendingCallWatcher *done) {
            QDBusPendingReply<QDBusVariant> reply=*done;
            done->deleteLater();
            // A sleep/wake/session event or a newer read retires this reply.
            if (request!=sleepRequest || boundary!=sessionGeneration || !available()) return;
            if (reply.isError()) {
                qWarning()<<"OWG_NATIVE sleep startup unavailable"<<reply.error().name();
                return;
            }
            if (reply.value().variant().metaType().id()!=QMetaType::Bool) return;
            sleepState=reply.value().variant().toBool()?"sleeping":"awake";
            sessionBoundary("native-sleep-bootstrap");
        });
    }
    void cancel(const QString &reason) {
        if (cancellationGeneration >= 9007199254740990ULL) {
            exhausted=true;emit availabilityChanged();
        } else ++cancellationGeneration;
        emit generationChanged();
        emit invalidated(reason);
    }
    void pruneOutputActivity() {
        QSet<QString> connected;
        for(auto *output:KWin::workspace()->outputs()) connected.insert(output->name());
        auto prune=[&connected](auto &values) {
            for(auto it=values.begin();it!=values.end();)
                if(!connected.contains(it.key()))it=values.erase(it);else ++it;
        };
        prune(outputCounts);prune(outputLastActivity);prune(outputInteractionCounts);
        prune(outputLastInteraction);prune(outputFocusCounts);
        // Geometry/focus may be reassigned during a disconnect. Preserve known
        // release state, and conservatively cover every survivor for any hold.
        if(buttonsDown)heldOutputs=connected;else heldOutputs.clear();
        for(auto it=heldKeyOutputs.begin();it!=heldKeyOutputs.end();++it)it.value()=connected;
        extraKeyOutputs.intersect(connected);
    }
    void resetActivity() {
        outputCounts.clear();outputLastActivity.clear();outputInteractionCounts.clear();outputLastInteraction.clear();
        heldOutputs.clear();heldKeyOutputs.clear();extraKeyOutputs.clear();interactionEpochMs=clock.elapsed();buttonKnown=false;buttonsDown=false;lastActivityMs=-1;
    }
    void sessionBoundary(const QString &reason) { ++sessionGeneration;resetActivity();cancel(reason); }
    bool focusKnown() const {
        if (!available()) return false;
        auto *focused=KWin::workspace()->activeWindow();
        return focused && !focused->isDeleted() && !focused->isInternal();
    }
    QSet<QString> focusedOutputs() const {
        QSet<QString> names;
        auto *focused=KWin::workspace()->activeWindow();
        if (focused && !focused->isDeleted() && focused->frameGeometry().isValid()) {
            const auto frame=focused->frameGeometry();
            for (auto *output:KWin::workspace()->outputs())
                if (frame.intersects(QRectF(output->geometry()))) names.insert(output->name());
        }
        // Unknown focus is not evidence that another display is unaffected.
        if (names.isEmpty()) for (auto *output:KWin::workspace()->outputs()) names.insert(output->name());
        return names;
    }
    void attribute(const QString &name) {
        if (outputCounts.contains(name) || outputCounts.size()<16) {
            ++outputCounts[name];outputLastActivity[name]=lastActivityMs;
        }
    }
    std::unique_ptr<ObserverSpy> spy;
    const QString epoch;
    QElapsedTimer clock;
    QMap<QString,quint64> outputCounts;
    QMap<QString,qint64> outputLastActivity,outputLastInteraction;
    QMap<QString,quint64> outputInteractionCounts,outputFocusCounts;
    QSet<QString> heldOutputs;
    QMap<quint32,QSet<QString>> heldKeyOutputs;
    QSet<QString> extraKeyOutputs;
    qint64 interactionEpochMs=0;
    quint64 sequence=0,topologyGeneration=0,sessionGeneration=0,focusGeneration=0,cancellationGeneration=0,sleepRequest=0;
    quint64 pointerCount=0,buttonCount=0,axisCount=0,keyCount=0,unknownFocusKeyCount=0;
    qint64 lastActivityMs=-1;
    bool installed=false,exhausted=false,buttonKnown=false,buttonsDown=false;
    bool otherInputObserved=false,unlockInputPending=false;
    QString lockState="unknown",sleepState="unknown",sessionActivity="unknown";
};
void ObserverSpy::pointerMotion(KWin::PointerMotionEvent *e) { owner->record(NativeObserver::Kind::Pointer,&e->position,e->buttons); }
void ObserverSpy::pointerButton(KWin::PointerButtonEvent *e) { owner->record(NativeObserver::Kind::Button,&e->position,e->buttons); }
void ObserverSpy::pointerAxis(KWin::PointerAxisEvent *e) { owner->record(NativeObserver::Kind::Axis,&e->position,e->buttons); }
void ObserverSpy::keyboardKey(KWin::KeyboardKeyEvent *event) { owner->keyboardEvent(event); }
#define OWG_OTHER_INPUT(method, event) void ObserverSpy::method(KWin::event *) { owner->otherInput(); }
OWG_OTHER_INPUT(touchDown, TouchDownEvent)
OWG_OTHER_INPUT(touchMotion, TouchMotionEvent)
OWG_OTHER_INPUT(touchUp, TouchUpEvent)
OWG_OTHER_INPUT(pinchGestureBegin, PointerPinchGestureBeginEvent)
OWG_OTHER_INPUT(pinchGestureUpdate, PointerPinchGestureUpdateEvent)
OWG_OTHER_INPUT(pinchGestureEnd, PointerPinchGestureEndEvent)
OWG_OTHER_INPUT(pinchGestureCancelled, PointerPinchGestureCancelEvent)
OWG_OTHER_INPUT(swipeGestureBegin, PointerSwipeGestureBeginEvent)
OWG_OTHER_INPUT(swipeGestureUpdate, PointerSwipeGestureUpdateEvent)
OWG_OTHER_INPUT(swipeGestureEnd, PointerSwipeGestureEndEvent)
OWG_OTHER_INPUT(swipeGestureCancelled, PointerSwipeGestureCancelEvent)
OWG_OTHER_INPUT(holdGestureBegin, PointerHoldGestureBeginEvent)
OWG_OTHER_INPUT(holdGestureEnd, PointerHoldGestureEndEvent)
OWG_OTHER_INPUT(holdGestureCancelled, PointerHoldGestureCancelEvent)
OWG_OTHER_INPUT(switchEvent, SwitchEvent)
OWG_OTHER_INPUT(tabletToolProximityEvent, TabletToolProximityEvent)
OWG_OTHER_INPUT(tabletToolAxisEvent, TabletToolAxisEvent)
OWG_OTHER_INPUT(tabletToolTipEvent, TabletToolTipEvent)
OWG_OTHER_INPUT(tabletToolButtonEvent, TabletToolButtonEvent)
OWG_OTHER_INPUT(tabletPadButtonEvent, TabletPadButtonEvent)
OWG_OTHER_INPUT(tabletPadStripEvent, TabletPadStripEvent)
OWG_OTHER_INPUT(tabletPadRingEvent, TabletPadRingEvent)
OWG_OTHER_INPUT(tabletPadDialEvent, TabletPadDialEvent)
#undef OWG_OTHER_INPUT

class ObserverPlugin : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:
    void registerTypes(const char *uri) override {
        qmlRegisterType<NativeObserver>(uri,1,0,"NativeObserver");
    }
};
#include "observer.moc"
