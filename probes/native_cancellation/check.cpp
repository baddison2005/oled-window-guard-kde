#include <kwin/effect/effect.h>
#include <kwin/effect/effecthandler.h>
#include <kwin/input.h>
#include <kwin/pointer_input.h>
#include <kwin/keyboard_input.h>
#include <kwin/touch_input.h>
#include <kwin/core/outputbackend.h>
#include <kwin/core/backendoutput.h>
#include <kwin/core/session.h>
#include <kwin/main.h>
#include <kwin/workspace.h>
#include <kwin/window.h>
#include <QCoreApplication>
#include <QDBusConnection>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJSValue>
#include <QJSValueIterator>
#include <cmath>
#include <QQmlComponent>
#include <QQmlContext>
#include <QQmlEngine>
#include <QPointer>
#include <QTimer>
#include <QDateTime>
#include <QFile>
#include <chrono>

// Separate, isolated test injector. Never linked into the production module.
class CancellationCheck : public KWin::Effect {
    Q_OBJECT
public:
    CancellationCheck() {
        registered=QDBusConnection::sessionBus().registerObject("/org/oledwindowguard/NativeCheck",
            "org.oledwindowguard.NativeCheck1",this,QDBusConnection::ExportAllSlots);
    }
    ~CancellationCheck() override {
        if (registered) QDBusConnection::sessionBus().unregisterObject("/org/oledwindowguard/NativeCheck");
    }
    QObject *observer() const { return qApp->property("owgNativeObserver1").value<QObject *>(); }
    static QJSValue js(QObject *o,const char *name) {
        const auto value=o->property(name);
        if (value.metaType()==QMetaType::fromType<QJSValue>()) return value.value<QJSValue>();
        return qmlEngine(o)->toScriptValue(value);
    }
public Q_SLOTS:
    QString Arm(const QString &kind, const QString &output) {
        auto *o=observer();
        if (!o || !o->parent() || !o->parent()->parent()) return "{}";
        if (armedObserver) disconnect(armedObserver,nullptr,this,nullptr);
        armedObserver=o;
        physicalKind=kind;physicalOutput=output;
        physicalResult.clear();
        QMetaObject::invokeMethod(o,"snapshot",Qt::DirectConnection,Q_RETURN_ARG(QVariantMap,physicalBaseline));
        physicalControllerBefore=js(o->parent()->parent(),"state").property("generation").toNumber();
        physicalHadProposal=!js(o->parent()->parent(),"proposal").isNull() && !js(o->parent()->parent(),"proposal").isUndefined();
        connect(o,SIGNAL(invalidated(QString)),this,SLOT(physicalInvalidated(QString)),Qt::DirectConnection);
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"armed",physicalHadProposal},{"baseline",physicalBaseline}}).toJson(QJsonDocument::Compact));
    }
    void physicalInvalidated(const QString &reason) {
        if (!physicalResult.isEmpty() || !armedObserver) return;
        auto *o=armedObserver.data(), *consumer=o->parent(), *controller=consumer->parent();
        QVariantMap fresh;
        QMetaObject::invokeMethod(o,"snapshot",Qt::DirectConnection,Q_RETURN_ARG(QVariantMap,fresh));
        QString counter=physicalKind=="press"?"button":physicalKind=="key"?"keyboard":physicalKind;
        const bool observed=fresh.value(counter).toULongLong()>physicalBaseline.value(counter).toULongLong();
        const double generation=o->property("cancellationGeneration").toDouble();
        bool tokenAfter=true;
        QMetaObject::invokeMethod(o,"isCurrent",Qt::DirectConnection,Q_RETURN_ARG(bool,tokenAfter),
            Q_ARG(QString,physicalBaseline.value("epoch").toString()),Q_ARG(double,physicalBaseline.value("cancellationGeneration").toDouble()));
        auto counts=[](const QVariantMap &sample,const QString &id) {
            for (const auto &row:sample.value("outputs").toList()) if (row.toMap().value("id").toString()==id) return row.toMap().value("events").toULongLong();
            return qulonglong(0);
        };
        const bool scoped=counts(fresh,physicalOutput)>counts(physicalBaseline,physicalOutput);
        const bool cleared=js(controller,"proposal").isNull() || js(controller,"proposal").isUndefined();
        const bool consumed=consumer->property("lastImmediateGeneration").toDouble()==generation;
        const bool advanced=js(controller,"state").property("generation").toNumber()>physicalControllerBefore;
        physicalResult={{"ok",physicalHadProposal && observed && scoped && !cleared && consumed && !advanced && !tokenAfter},
            {"kind",physicalKind},{"output",physicalOutput},{"reason",reason},{"observed",observed},{"scoped",scoped},
            {"hadProposal",physicalHadProposal},{"proposalCleared",cleared},{"oldTokenRejected",!tokenAfter},
            {"synchronous",consumed && !advanced},{"baseline",physicalBaseline},{"fresh",fresh}};
    }
    QString PhysicalResult() const {
        return QString::fromUtf8(QJsonDocument::fromVariant(physicalResult).toJson(QJsonDocument::Compact));
    }
    QString Inspect() const {
        auto *o=observer();
        QVariantMap snapshot;
        if (o) QMetaObject::invokeMethod(o,"snapshot",Qt::DirectConnection,Q_RETURN_ARG(QVariantMap,snapshot));
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{
            {"present",bool(o)},{"snapshot",snapshot},{"applicationName",qApp->applicationName()},
            {"applicationVersion",qApp->applicationVersion()}}).toJson(QJsonDocument::Compact));
    }
    QString Verify(const QString &kind) {
        auto reply=[](const QVariantMap &value) { return QString::fromUtf8(QJsonDocument::fromVariant(value).toJson(QJsonDocument::Compact)); };
        auto *o=observer();
        if (!o || !o->property("available").toBool() || !o->parent() || !o->parent()->parent())
            return reply({{"ok",false},{"reason","native observer unavailable"}});
        auto *consumer=o->parent(), *controller=consumer->parent();
        const auto epoch=o->property("epoch").toString();
        const double generation=o->property("cancellationGeneration").toDouble();
        const auto before=js(controller,"state").property("generation").toNumber();
        const bool hadProposal=!js(controller,"proposal").isNull() && !js(controller,"proposal").isUndefined();
        bool tokenBefore=false,tokenAfter=true;
        QMetaObject::invokeMethod(o,"isCurrent",Qt::DirectConnection,Q_RETURN_ARG(bool,tokenBefore),Q_ARG(QString,epoch),Q_ARG(double,generation));
        auto now=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch());
        if (kind=="pointer") KWin::input()->pointer()->processMotionAbsolute(QPointF(50,50),now);
        else if (kind=="press" || kind=="release")
            KWin::input()->pointer()->processButton(0x110,kind=="press"?KWin::PointerButtonState::Pressed:KWin::PointerButtonState::Released,now);
        else if (kind=="axis") KWin::input()->pointer()->processAxis(KWin::PointerAxis::Vertical,-15,-120,KWin::PointerAxisSource::Wheel,false,now);
        else if (kind=="key") {
            KWin::input()->keyboard()->processKey(30,KWin::KeyboardKeyState::Pressed,now);
            KWin::input()->keyboard()->processKey(30,KWin::KeyboardKeyState::Released,now+std::chrono::microseconds(1000));
        } else if (kind=="touch") {
            KWin::input()->touch()->processDown(7,QPointF(50,50),now);
            KWin::input()->touch()->processUp(7,now+std::chrono::microseconds(1000));
        } else if (kind=="focus") KWin::workspace()->windowActivated(KWin::workspace()->activeWindow());
        else if (kind=="locking") KWin::effects->screenAboutToLock();
        else if (kind=="locked" || kind=="unlocked") KWin::effects->screenLockingChanged(kind=="locked");
        else if (kind=="sleep" || kind=="wake" || kind=="inactive" || kind=="active") {
            auto *session=KWin::kwinApp()->session();
            if (!session || !session->inherits("KWin::NoopSession")) return reply({{"ok",false},{"reason","requires private NoopSession"}});
            if (kind=="sleep") session->aboutToSleep();
            else if (kind=="wake") session->awoke();
            else session->activeChanged(kind=="active");
        } else if (kind=="topology") {
            auto *output=KWin::kwinApp()->outputBackend()->createVirtualOutput("OWG-Native-Check","Isolated cancellation test",QSize(640,480),1.0);
            if (!output) return reply({{"ok",false},{"reason","virtual output unavailable"}});
            KWin::kwinApp()->outputBackend()->removeVirtualOutput(output);
        } else return reply({{"ok",false},{"reason","unknown check"}});
        // This runs immediately after processMotion/Button/Axis/Key or the
        // lifecycle signal returns, without processing events, polling or IPC.
        const double afterGeneration=o->property("cancellationGeneration").toDouble();
        const double consumed=consumer->property("lastImmediateGeneration").toDouble();
        const double after=js(controller,"state").property("generation").toNumber();
        const bool proposalCleared=controller->property("proposal").isNull() || js(controller,"proposal").isNull();
        QMetaObject::invokeMethod(o,"isCurrent",Qt::DirectConnection,Q_RETURN_ARG(bool,tokenAfter),Q_ARG(QString,epoch),Q_ARG(double,generation));
        QVariantMap snapshot;
        QMetaObject::invokeMethod(o,"snapshot",Qt::DirectConnection,Q_RETURN_ARG(QVariantMap,snapshot));
        const bool benign=QStringList{"pointer","press","axis","key","release","focus"}.contains(kind);
        const bool expectCancel=!benign || (!js(consumer,"outputConnectors").toVariant().toList().isEmpty() && kind!="pointer");
        const bool ok=tokenBefore && !tokenAfter && afterGeneration>generation && consumed==afterGeneration &&
            (expectCancel?(after>before && proposalCleared):(after==before && !proposalCleared));
        return reply({{"ok",ok},{"kind",kind},{"nativeBefore",generation},{"nativeAfter",afterGeneration},
            {"consumedGeneration",consumed},{"controllerBefore",before},{"controllerAfter",after},
            {"hadProposal",hadProposal},{"proposalCleared",proposalCleared},{"oldTokenRejected",!tokenAfter},
            {"snapshot",snapshot},{"synchronous",true}});
    }
    QString Interact(const QString &kind,const QString &connector) {
        if (!isolatedWriterContext()) return "{}";
        auto *output=KWin::workspace()->outputAt(QPointF(0,0));
        bool found=false;
        for (auto *candidate:KWin::workspace()->outputs()) if (candidate->name()==connector) {output=candidate;found=true;}
        if (!found || !output) return "{}";
        const auto geometry=output->geometry();
        auto point=QPointF(geometry.x()+geometry.width()-12,geometry.y()+geometry.height()-12);
        auto now=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch());
        if (kind=="focus") {
            for (auto *window:KWin::workspace()->windows()) if (window->isNormalWindow() && window->frameGeometry().width()>50 && !window->isMinimized() &&
                    window->resourceClass()==QStringLiteral("oled-window-guard-runtime-test") &&
                    geometry.contains(window->frameGeometry().center().toPoint())) {
                KWin::workspace()->activateWindow(window,true);break;
            }
        } else {
            if (kind!="pointer" && kind!="desktopClick") for (auto *window:KWin::workspace()->windows())
                if (window->isNormalWindow() && window->frameGeometry().width()>50 && !window->isMinimized() && window->resourceClass()==QStringLiteral("oled-window-guard-runtime-test") &&
                        geometry.contains(window->frameGeometry().center().toPoint())) {point=window->frameGeometry().center();break;}
            KWin::input()->pointer()->processMotionAbsolute(point,now);
            if (kind=="pointer") KWin::input()->pointer()->processMotionAbsolute(point+QPointF(-2,0),now+std::chrono::microseconds(1000));
            else if (kind=="scroll") KWin::input()->pointer()->processAxis(KWin::PointerAxis::Vertical,-15,-120,KWin::PointerAxisSource::Wheel,false,now);
            else if (kind=="press" || kind=="drag" || kind=="desktopClick" || kind=="hold") {
                KWin::input()->pointer()->processButton(0x110,KWin::PointerButtonState::Pressed,now);
                if (kind=="drag") KWin::input()->pointer()->processMotionAbsolute(point+QPointF(-2,0),now+std::chrono::microseconds(1000));
                if (kind!="hold") KWin::input()->pointer()->processButton(0x110,KWin::PointerButtonState::Released,now+std::chrono::microseconds(2000));
            } else if (kind=="release") KWin::input()->pointer()->processButton(0x110,KWin::PointerButtonState::Released,now);
            else if (kind=="keyHold") KWin::input()->keyboard()->processKey(30,KWin::KeyboardKeyState::Pressed,now);
            else if (kind=="keyRelease") KWin::input()->keyboard()->processKey(30,KWin::KeyboardKeyState::Released,now);
            else if (kind=="key") {
                KWin::input()->keyboard()->processKey(30,KWin::KeyboardKeyState::Pressed,now);
                KWin::input()->keyboard()->processKey(30,KWin::KeyboardKeyState::Released,now+std::chrono::microseconds(1000));
            }
        }
        auto *runtime=oRoot()->property("runtime").value<QObject *>();
        auto self=qmlEngine(runtime)->newQObject(runtime);
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"runtime",self.property("status").callWithInstance(self).toVariant()},
            {"scope",js(observer()->parent(),"outputConnectors").toVariant()}, {"desktop",desktopSnapshot()}}).toJson(QJsonDocument::Compact));
    }
    QString DimmingStatus() const {
        if (!isolatedWriterContext()) return "{}";
        auto *dimmer=oRoot()->property("dimming").value<QObject *>();
        QVariantMap masks,planes;
        const auto surfaces=js(dimmer,"surfaces");
        QJSValueIterator it(surfaces);
        while (it.hasNext()) {
            it.next();const auto surface=it.value();masks[it.name()]=surface.property("masks").toVariant();
            QVariantList maskItems;
            const auto children=surface.property("plane").property("mainItem").property("children");
            for (int i=0;i<children.property("length").toInt();++i) {
                const auto child=children.property(i);
                if (child.property("objectName").toString().startsWith("dimmingMask"))
                    maskItems.append(QVariantMap{{"id",QString::number(quintptr(child.toQObject()))},{"visible",child.property("visible").toBool()},{"opacity",child.property("opacity").toNumber()}});
            }
            int stacking=-1;
            for (auto *window:KWin::workspace()->windows()) if (window->isInternal() && window->caption()==surface.property("caption").toString()) stacking=window->stackingOrder();
            planes[it.name()]=QVariantMap{{"confirmed",surface.property("confirmed").toBool()},{"visible",surface.property("plane").property("visible").toBool()},{"stacking",stacking},{"slots",maskItems}};
        }
        QVariantMap native;QMetaObject::invokeMethod(observer(),"snapshot",Qt::DirectConnection,Q_RETURN_ARG(QVariantMap,native));
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"state",js(dimmer,"state").toVariant()},
            {"masks",masks},{"planes",planes},{"native",native}}).toJson(QJsonDocument::Compact));
    }
    QString WarningStatus() const {
        if (!isolatedWriterContext()) return "{}";
        auto *surface=oRoot()->property("warningSurface").value<QObject *>();
        auto *item=js(surface,"warning").property("mainItem").toQObject();
        auto *label=item?item->findChild<QObject *>("warningCountdown"):nullptr;
        int stacking=-1;
        for (auto *window:KWin::workspace()->windows()) if (window->isInternal() && window->caption()=="OLED Window Guard warning") stacking=window->stackingOrder();
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"remaining",surface->property("remainingSeconds")},
            {"grace",surface->property("graceRemainingSeconds")},{"generation",surface->property("activeGeneration")},
            {"confirmed",surface->property("placementConfirmed")},{"text",label?label->property("text"):QVariant()},
            {"stacking",stacking}}).toJson(QJsonDocument::Compact));
    }
    QString PassiveVisual(const QString &action) {
        if (!isolatedWriterContext()) return "{}";
        if (action=="hide") {delete passiveVisual;passiveVisual=nullptr;return "{}";}
        if (!passiveVisual) {
            auto *engine=qmlEngine(oRoot());QQmlComponent component(engine);
            component.setData("import QtQuick; Window { title: \"OWG passive compositor fixture\"; color: \"transparent\"; width:900; height:650; flags:Qt.Window | Qt.FramelessWindowHint | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus; visible:true }",QUrl("file:///tmp/owg-passive-test.qml"));
            passiveVisual=component.create();
        }
        bool passive=false;
        for (auto *window:KWin::workspace()->windows()) if (window->caption()=="OWG passive compositor fixture")
            QMetaObject::invokeMethod(observer(),"isPassiveOverlay",Qt::DirectConnection,Q_RETURN_ARG(bool,passive),Q_ARG(QString,window->internalId().toString()));
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"created",bool(passiveVisual)},{"passive",passive},{"desktop",desktopSnapshot()}}).toJson(QJsonDocument::Compact));
    }
    QString MoveActive(double dx,double dy) {
        if (!isolatedWriterContext() || !std::isfinite(dx) || !std::isfinite(dy) || std::abs(dx)>50 || std::abs(dy)>50) return "{}";
        auto *window=KWin::workspace()->activeWindow();
        if (!window || window->resourceClass()!=QStringLiteral("oled-window-guard-runtime-test")) return "{}";
        const auto before=frameMap(window);const auto rect=window->frameGeometry();
        window->moveResize(KWin::RectF(rect.x()+dx,rect.y()+dy,rect.width(),rect.height()));
        auto *root=oRoot();auto self=qmlEngine(root)->newQObject(root);
        const auto identity=self.property("identity").callWithInstance(self,{qmlEngine(root)->newQObject(window)}).toString();
        writeHistory.append(QVariantMap{{"actor","test-external"},{"identity",identity},{"from",before},{"to",frameMap(window)}});
        return "{\"moved\":true}";
    }
    QString InteractiveMove(const QString &action,double dx,double dy) {
        if (!isolatedWriterContext() || !std::isfinite(dx) || !std::isfinite(dy) || std::abs(dx)>50 || std::abs(dy)>50) return "{}";
        auto *window=KWin::workspace()->activeWindow();
        if (!window || window->resourceClass()!=QStringLiteral("oled-window-guard-runtime-test")) return "{}";
        auto now=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch());
        if (action=="begin") {
            disconnect(moveAudit);
            auto *root=oRoot();auto self=qmlEngine(root)->newQObject(root);
            const auto identity=self.property("identity").callWithInstance(self,{qmlEngine(root)->newQObject(window)}).toString();
            moveAuditFrame=frameMap(window);
            moveAudit=connect(window,&KWin::Window::frameGeometryChanged,this,[this,window,identity]() {
                const auto after=frameMap(window);
                writeHistory.append(QVariantMap{{"actor","test-external"},{"identity",identity},{"from",moveAuditFrame},{"to",after}});
                moveAuditFrame=after;
            });
            const auto rect=window->frameGeometry();
            KWin::input()->pointer()->processMotionAbsolute(QPointF(rect.x()+rect.width()/2,rect.y()+10),now);
            KWin::input()->pointer()->processButton(0x110,KWin::PointerButtonState::Pressed,now);
            KWin::workspace()->slotWindowMove();
        } else if (action=="step") {
            const auto rect=window->frameGeometry();
            KWin::input()->pointer()->processMotionAbsolute(KWin::input()->globalPointer()+QPointF(dx,dy),now);
        } else if (action=="end") {
            KWin::input()->keyboard()->processKey(28,KWin::KeyboardKeyState::Pressed,now);
            KWin::input()->keyboard()->processKey(28,KWin::KeyboardKeyState::Released,now);
            KWin::input()->pointer()->processButton(0x110,KWin::PointerButtonState::Released,now);
            disconnect(moveAudit);
        } else return "{}";
        auto *dimmer=oRoot()->property("dimming").value<QObject *>();
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"interactive",(window->isInteractiveMove() || window->isInteractiveResize())},
            {"dimming",js(dimmer,"status").call().toVariant()},{"details",DimmingStatus()},{"desktop",desktopSnapshot()}}).toJson(QJsonDocument::Compact));
    }
    QString PrepareWriter() {
        auto *session=KWin::kwinApp()->session();
        if (!session || !session->inherits("KWin::NoopSession")) return "{}";
        KWin::effects->screenLockingChanged(false);
        session->awoke();session->activeChanged(true);
        auto now=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch());
        for (auto *output:KWin::workspace()->outputs()) {
            const auto g=output->geometry();
            KWin::input()->pointer()->processMotionAbsolute(QPointF(g.x()+g.width()-10,g.y()+g.height()-10),now);
        }
        KWin::input()->pointer()->processButton(0x110,KWin::PointerButtonState::Released,now);
        return Inspect();
    }
    QObject *writer() const {
        auto *o=observer();
        if (!o || !o->parent() || !o->parent()->parent() || !o->parent()->parent()->parent()) return nullptr;
        return o->parent()->parent()->parent()->property(qgetenv("OWG_TEST_ORDERED_WRITER")=="1"?"orderedWriter":"writer").value<QObject *>();
    }
    bool isolatedWriterContext() const {
        auto *session=KWin::kwinApp()->session();
        return qgetenv("OWG_NATIVE_CHECK_ISOLATED")=="1" && session && session->inherits("KWin::NoopSession");
    }
    QString WriterArmFault(const QString &kind) {
        if (!isolatedWriterContext()) return "{}";
        auto *w=writer();
        if (!w || !QStringList{"none","timeout","input","external","constraint","ordered-settle-0","ordered-timeout-0","ordered-timeout-1","ordered-timeout-2","ordered-input-1","ordered-external-1","ordered-rollback-timeout"}.contains(kind)) return "{}";
        if (watchedWriter!=w) {
            if (watchedWriter) disconnect(watchedWriter,nullptr,this,nullptr);
            watchedWriter=w;
            connect(w,SIGNAL(stateChanged()),this,SLOT(writerStateChanged()),Qt::DirectConnection);
            connect(w,SIGNAL(lastWriteChanged()),this,SLOT(writerWriteChanged()),Qt::DirectConnection);
        }
        faultKind=kind;faultFired=false;faultReceipt.clear();
        return "{\"armed\":true}";
    }
    void writerWriteChanged() {
        auto *w=watchedWriter.data();
        if (w) {
            auto record=js(w,"lastWrite").toVariant().toMap();
            const auto desktop=desktopSnapshot().toMap();
            for (const auto &window:desktop.value("windows").toList()) {
                const auto row=window.toMap();
                if (row.value("identity")==record.value("identity")) record["observed"]=row.value("rect");
            }
            if (writeHistory.size()<256) writeHistory.append(record);
            if (qgetenv("OWG_TEST_ORDERED_WRITER")=="1") orderedWriteFault(w,record);
        }
    }
    void orderedWriteFault(QObject *w,const QVariantMap &record) {
        const bool reverse=record.value("rollback").toBool();
        const int index=record.value("stepIndex").toInt();
        const bool repeat=faultKind=="ordered-rollback-timeout";
        const bool match=repeat ? ((!reverse && index==1) || (reverse && index==0)) :
            (!faultFired && !reverse && faultKind.endsWith(QString::number(index)));
        if (!faultKind.startsWith("ordered-") || !match) return;
        faultFired=true;
        const auto generation=record.value("generation").toDouble();
        const auto command=record.value("commandId").toString();
        faultReceipt={{"kind",faultKind},{"stepIndex",index},{"rollback",reverse},{"generation",generation},{"commandId",command}};
        if (faultKind=="ordered-input-1") {
            auto *o=observer();const auto before=o->property("cancellationGeneration").toDouble();
            auto now=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch());
            KWin::input()->pointer()->processMotionAbsolute(QPointF(50,50),now);
            faultReceipt["synchronousCanceled"]=js(w,"state").property("phase").toString()=="Paused" && o->property("cancellationGeneration").toDouble()>before;
            return;
        }
        if (faultKind=="ordered-settle-0") {
            auto *o=observer();auto root=qmlEngine(o)->newQObject(oRoot());
            for (auto *target:KWin::workspace()->windows()) {
                const auto id=root.property("identity").callWithInstance(root,{qmlEngine(o)->newQObject(target)}).toString();
                if (id!=record.value("identity").toString()) continue;
                const auto final=target->frameGeometry();const auto before=frameMap(target);
                target->moveResize(KWin::RectF(final.x()+2,final.y(),final.width(),final.height()));
                writeHistory.append(QVariantMap{{"actor","test-external"},{"identity",id},{"from",before},{"to",frameMap(target)}});
                QTimer::singleShot(180,this,[this,guard=QPointer<KWin::Window>(target),id,final] {
                    if (!guard) return;const auto before=frameMap(guard);
                    guard->moveResize(final);
                    writeHistory.append(QVariantMap{{"actor","test-external"},{"identity",id},{"from",before},{"to",frameMap(guard)}});
                });break;
            }
            return;
        }
        if (faultKind=="ordered-external-1") {
            auto *o=observer();auto root=qmlEngine(o)->newQObject(oRoot());
            for (auto *target:KWin::workspace()->windows()) {
                auto id=root.property("identity").callWithInstance(root,{qmlEngine(o)->newQObject(target)}).toString();
                if (id!=record.value("identity").toString()) continue;
                auto before=frameMap(target);const auto r=target->frameGeometry();
                target->moveResize(KWin::RectF(r.x()+10,r.y()+10,r.width(),r.height()));
                writeHistory.append(QVariantMap{{"actor","test-external"},{"identity",id},{"from",before},{"to",frameMap(target)}});
                faultReceipt["externalFrame"]=frameMap(target);break;
            }
            return;
        }
        auto *timer=w->property("verification").value<QObject *>();timer->setProperty("running",false);
        QTimer::singleShot(750,this,[this,guard=QPointer<QObject>(w),generation,command] {
            if (!guard || js(guard,"state").property("generation").toNumber()!=generation) return;
            auto self=qmlEngine(guard)->newQObject(guard);
            QVariantMap event{{"type","stepTimeout"},{"generation",generation},{"commandId",command},{"now",double(QDateTime::currentMSecsSinceEpoch())}};
            self.property("feed").callWithInstance(self,{qmlEngine(guard)->toScriptValue(event)});
            // A second withheld reverse has its own timer; do not restart it.
            if (!js(guard,"state").property("tx").property("rollback").toBool() || faultKind!="ordered-rollback-timeout")
                guard->property("verification").value<QObject *>()->setProperty("running",true);
        });
    }
    static QVariantMap frameMap(KWin::Window *window) {
        const auto r=window->frameGeometry();
        return {{"x",r.x()},{"y",r.y()},{"width",r.width()},{"height",r.height()}};
    }
    void writerStateChanged() {
        auto *w=watchedWriter.data();
        if (!w || qgetenv("OWG_TEST_ORDERED_WRITER")=="1" || faultFired || faultKind=="none" || js(w,"state").property("phase").toString()!="Settling") return;
        auto state=js(w,"state"),tx=state.property("transaction");
        if (faultKind=="timeout" && tx.property("observations").property("length").toInt()<1) return;
        faultFired=true;
        const auto generation=state.property("generation").toNumber();
        const auto command=tx.property("writeCommandId").toString();
        const auto identity=tx.property("move").property("identity").toString();
        auto *o=observer();auto root=qmlEngine(o)->newQObject(o->parent()->parent()->parent());
        KWin::Window *target=nullptr;
        for (auto *window:KWin::workspace()->windows()) {
            auto id=root.property("identity").callWithInstance(root,{qmlEngine(o)->newQObject(window)}).toString();
            if (id==identity) {target=window;break;}
        }
        faultReceipt={{"kind",faultKind},{"phase","Settling"},{"generation",generation},
            {"commandId",command},{"identity",identity},{"acceptedReadbacks",tx.property("observations").property("length").toInt()},{"nativeBefore",o->property("cancellationGeneration")}};
        if (!target) {faultReceipt["error"]="target missing";return;}
        faultReceipt["frameBefore"]=frameMap(target);
        if (faultKind=="input") {
            auto now=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch());
            KWin::input()->pointer()->processMotionAbsolute(QPointF(50,50),now);
            faultReceipt["synchronousCanceled"]=js(w,"state").property("phase").toString()=="Paused" &&
                o->property("cancellationGeneration").toDouble()>faultReceipt.value("nativeBefore").toDouble();
        } else if (faultKind=="external") {
            const auto before=target->frameGeometry();
            target->moveResize(KWin::RectF(before.x()+35,before.y()+35,before.width(),before.height()));
            auto record=js(w,"lastWrite").toVariant().toMap();
            record["actor"]="test-external";record["from"]=faultReceipt.value("frameBefore");record["to"]=frameMap(target);
            writeHistory.append(record);faultReceipt["externalFrame"]=frameMap(target);
        } else {
            auto *timer=w->property("verification").value<QObject *>();
            timer->setProperty("running",false);
            if (faultKind=="constraint") {
                const auto name=qgetenv("OWG_WRITER_CLIENT_TRIGGER");
                QFile file(QString::fromUtf8(name));
                if (!name.startsWith("/tmp/owg-kwin-adapter-") || !file.open(QIODevice::WriteOnly|QIODevice::Truncate)) {
                    faultReceipt["error"]="private client trigger unavailable";timer->setProperty("running",true);return;
                }
                file.write("grow");file.close();
            }
            const auto kind=faultKind;
            QTimer::singleShot(kind=="timeout"?750:200,this,[this,guard=QPointer<QObject>(w),target=QPointer<KWin::Window>(target),generation,command,kind] {
                if (!guard || js(guard,"state").property("generation").toNumber()!=generation) return;
                if (kind=="constraint" && target) {
                    auto record=js(guard,"lastWrite").toVariant().toMap();
                    record["actor"]="test-client-constraint";record["from"]=faultReceipt.value("frameBefore");record["to"]=frameMap(target);
                    writeHistory.append(record);faultReceipt["clientFrame"]=frameMap(target);
                }
                if (kind=="timeout") {
                    auto self=qmlEngine(guard)->newQObject(guard);
                    QVariantMap event{{"type","settlementDeadline"},{"generation",generation},{"commandId",command},
                        {"now",double(QDateTime::currentMSecsSinceEpoch())}};
                    self.property("feed").callWithInstance(self,{qmlEngine(guard)->toScriptValue(event)});
                }
                guard->property("verification").value<QObject *>()->setProperty("running",true);
            });
        }
    }
    QString WriterTarget(const QString &kind) {
        if (!isolatedWriterContext()) return "{}";
        auto *o=observer();if (!o || !o->parent()) return "{}";
        auto root=qmlEngine(o)->newQObject(o->parent()->parent()->parent());
        for (auto *window:KWin::workspace()->windows()) {
            const bool wanted=kind=="xwayland"?window->property("resourceClass").toByteArray().toLower()=="xterm":
                window->property("caption").toString()=="OLED Window Guard owned preview candidate";
            if (!wanted) continue;
            auto id=root.property("identity").callWithInstance(root,{qmlEngine(o)->newQObject(window)}).toString();
            if (kind=="xwayland") {
                auto before=frameMap(window);window->setMinimized(false);
                const auto r=window->frameGeometry();
                window->moveResize(KWin::RectF(620,350,r.width(),r.height()));
                writeHistory.append(QVariantMap{{"identity",id},{"actor","test-setup"},{"from",before},{"to",frameMap(window)}});
            }
            return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"identity",id},{"frame",frameMap(window)},
                {"appId",window->property("desktopFileName")},{"kind",kind}}).toJson(QJsonDocument::Compact));
        }
        return "{}";
    }
    QString OrderedSetup() {
        if (!isolatedWriterContext() || qgetenv("OWG_TEST_ORDERED_WRITER")!="1") return "{}";
        auto *o=observer();if (!o) return "{}";
        auto root=qmlEngine(o)->newQObject(oRoot());QVariantList targets;
        const auto origin=KWin::workspace()->outputs().first()->geometry();
        for (auto *window:KWin::workspace()->windows()) {
            const auto caption=window->property("caption").toString();
            const bool first=caption=="OLED Window Guard owned preview candidate";
            if (!first && caption!="OLED Window Guard owned ordered candidate") continue;
            auto id=root.property("identity").callWithInstance(root,{qmlEngine(o)->newQObject(window)}).toString();
            auto before=frameMap(window);
            window->moveResize(KWin::RectF(origin.x()+(first?30:330),origin.y()+160,220,128));
            auto after=frameMap(window);
            writeHistory.append(QVariantMap{{"actor","test-setup"},{"identity",id},{"from",before},{"to",after}});
            targets.append(QVariantMap{{"identity",id},{"frame",after},{"first",first}});
        }
        return QString::fromUtf8(QJsonDocument::fromVariant(targets).toJson(QJsonDocument::Compact));
    }
    QString AdjacentSetup() { return adjacentSetup(false); }
    QString AdjacentContactSetup() { return adjacentSetup(true); }
    QString adjacentSetup(bool contact) {
        if (!isolatedWriterContext() || qgetenv("OWG_TEST_ADJACENT_ROUNDING")!="1") return "{}";
        auto *o=observer();if (!o) return "{}";
        auto root=qmlEngine(o)->newQObject(oRoot());QVariantList targets;
        const auto area=KWin::workspace()->outputs().first()->geometry();
        for (auto *window:KWin::workspace()->windows()) {
            if (window->resourceClass()!=QStringLiteral("oled-window-guard-runtime-test")) continue;
            const auto caption=window->caption();
            const bool first=caption==QStringLiteral("OLED Window Guard owned preview candidate");
            if (!first && caption!=QStringLiteral("OLED Window Guard owned ordered candidate")) continue;
            const auto id=root.property("identity").callWithInstance(root,{qmlEngine(o)->newQObject(window)}).toString();
            const auto before=frameMap(window);const int leftWidth=area.width()/2-1;
            window->moveResize(KWin::RectF(area.x()+(first?0:leftWidth-(contact?1:0)),area.y()+100,first?leftWidth:area.width()-leftWidth,200));
            const auto after=frameMap(window);
            writeHistory.append(QVariantMap{{"actor","test-setup"},{"identity",id},{"from",before},{"to",after}});
            targets.append(QVariantMap{{"identity",id},{"frame",after},{"first",first}});
        }
        return QString::fromUtf8(QJsonDocument::fromVariant(targets).toJson(QJsonDocument::Compact));
    }
    QString TopologyOutput(const QString &action,const QString &name) {
        if(!isolatedWriterContext() || qgetenv("OWG_TEST_OUTPUT_RETENTION")!="1")return "{}";
        // KWin itself relocates disposable clients on output removal. Record
        // each observed frame transition; no geometry is requested by this spy.
        auto *root=oRoot();auto self=qmlEngine(root)->newQObject(root);
        for(auto *window:KWin::workspace()->windows()) {
            if(window->resourceClass()!="oled-window-guard-runtime-test" && window->resourceClass().toLower()!="xterm")continue;
            const auto id=self.property("identity").callWithInstance(self,{qmlEngine(root)->newQObject(window)}).toString();
            if(topologyTracked.contains(id))continue;
            topologyTracked.insert(id);
            connect(window,&KWin::Window::frameGeometryChanged,this,[this,window,id](const KWin::RectF &before) {
                const QVariantMap from{{"x",before.x()},{"y",before.y()},{"width",before.width()},{"height",before.height()}};
                writeHistory.append(QVariantMap{{"actor","test-topology-observed"},{"identity",id},{"from",from},{"to",frameMap(window)}});
            });
        }
        if(action=="add") {
            if(!KWin::kwinApp()->outputBackend()->createVirtualOutput(name.startsWith("Virtual-")?name.mid(8):name,"OWG isolated output",QSize(900,650),1.0))return "{}";
        } else if(action=="remove") {
            auto outputs=KWin::kwinApp()->outputBackend()->outputs();
            if(outputs.size()<=1)return "{}";
            for(auto *output:outputs)if(output->name()==name || output->name()=="Virtual-"+name) {
                KWin::kwinApp()->outputBackend()->removeVirtualOutput(output);break;
            }
        } else return "{}";
        return WriterStatus();
    }
    QString GuardControls(const QString &action) {
        if(!isolatedWriterContext() || qgetenv("OWG_TEST_GUARD_CONTROLS")!="1") return "{}";
        auto *runtime=oRoot()->property("runtime").value<QObject *>();
        if(!runtime)return "{}";
        auto scheduler=js(runtime,"scheduler");
        if(action=="due") {
            auto deadlines=scheduler.property("deadlines");
            const auto keys=deadlines.toVariant().toMap().keys();
            for(const auto &key:keys)deadlines.setProperty(key,0.0);
        }
        auto *warning=oRoot()->property("warningSurface").value<QObject *>();
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{
            {"guards",js(runtime,"guards").toVariant()},{"scheduler",scheduler.toVariant()},
            {"warningSerial",runtime->property("warningSerial")},{"warningActive",warning->property("activeGeneration")}}).toJson(QJsonDocument::Compact));
    }
    QString GroupedSwapSetup() {
        if (!isolatedWriterContext() || qgetenv("OWG_TEST_GROUPED_ARRANGEMENTS")!="1") return "{}";
        auto *o=observer();if (!o) return "{}";
        auto root=qmlEngine(o)->newQObject(oRoot());QVariantList targets;
        const auto area=KWin::workspace()->outputs().first()->geometry();
        for (auto *window:KWin::workspace()->windows()) {
            if (window->resourceClass()!=QStringLiteral("oled-window-guard-runtime-test")) continue;
            const auto caption=window->caption();KWin::RectF target;
            if(caption==QStringLiteral("OLED Window Guard owned preview candidate")) target=KWin::RectF(area.x()+599,area.y()+250,301,200);
            else if(caption==QStringLiteral("OLED Window Guard owned ordered candidate")) target=KWin::RectF(area.x()+599,area.y()+450,301,200);
            else if(caption==QStringLiteral("OLED Window Guard owned grouped tall candidate")) target=KWin::RectF(area.x(),area.y(),300,650);
            else if(caption==QStringLiteral("OLED Window Guard owned grouped writer candidate")) target=KWin::RectF(area.x()+300,area.y(),299,400);
            else continue;
            const auto id=root.property("identity").callWithInstance(root,{qmlEngine(o)->newQObject(window)}).toString();
            const auto before=frameMap(window);window->moveResize(target);const auto after=frameMap(window);
            writeHistory.append(QVariantMap{{"actor","test-setup"},{"identity",id},{"from",before},{"to",after}});
            targets.append(QVariantMap{{"identity",id},{"frame",after}});
        }
        return QString::fromUtf8(QJsonDocument::fromVariant(targets).toJson(QJsonDocument::Compact));
    }
    QString GroupSetup(const QString &kind) {
        if (!isolatedWriterContext() || qgetenv("OWG_TEST_GROUP_RESTORE")!="1" || !QStringList{"drift","rotation","shrink","external"}.contains(kind)) return "{}";
        auto *o=observer();if (!o) return "{}";
        auto root=qmlEngine(o)->newQObject(oRoot());QVariantList targets;
        const auto origin=KWin::workspace()->outputs().first()->geometry();
        for (auto *window:KWin::workspace()->windows()) {
            const auto caption=window->property("caption").toString();
            const bool first=caption=="OLED Window Guard owned preview candidate";
            if (!first && caption!="OLED Window Guard owned ordered candidate") continue;
            auto id=root.property("identity").callWithInstance(root,{qmlEngine(o)->newQObject(window)}).toString();
            auto before=frameMap(window);const auto r=window->frameGeometry();
            if (kind=="external") window->moveResize(KWin::RectF(r.x()+5,r.y()+5,r.width(),r.height()));
            else if (kind=="shrink") window->moveResize(KWin::RectF(origin.x()+(first?origin.width()+30:300),origin.y()+(first?440:160),first?220:301,128));
            else window->moveResize(KWin::RectF(origin.x()+(first?30:530),origin.y()+160,220,128));
            auto after=frameMap(window);
            writeHistory.append(QVariantMap{{"actor",kind=="external"?"test-external":"test-setup"},{"identity",id},{"from",before},{"to",after}});
            targets.append(QVariantMap{{"identity",id},{"frame",after},{"first",first}});
        }
        return QString::fromUtf8(QJsonDocument::fromVariant(targets).toJson(QJsonDocument::Compact));
    }
    QString WriterRestore() {
        if (!isolatedWriterContext()) return "{}";
        auto *w=writer();if (!w) return "{}";
        auto self=qmlEngine(w)->newQObject(w);
        const auto accepted=self.property("restore").callWithInstance(self);
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"accepted",accepted.toBool()},
            {"state",js(w,"state").toVariant()},{"writeCount",w->property("writeCount")},{"lastFailure",w->property("lastFailure")}}).toJson(QJsonDocument::Compact));
    }
    QString WriterObserve(const QString &kind) {
        if (!isolatedWriterContext() || qgetenv("OWG_TEST_IMPORTED_PRODUCT")!="1" ||
                !QStringList{"single","ordered"}.contains(kind)) return "{}";
        auto *w=oRoot()->property(kind=="single"?"writer":"orderedWriter").value<QObject *>();
        if (!w) return "{}";
        if (watchedWriter) disconnect(watchedWriter,nullptr,this,nullptr);
        watchedWriter=w;
        connect(w,SIGNAL(lastWriteChanged()),this,SLOT(writerWriteChanged()),Qt::DirectConnection);
        return "{\"observing\":true}";
    }
    QString SingleStart() {
        if (!isolatedWriterContext() || qgetenv("OWG_TEST_GROUP_RESTORE")!="1") return "{}";
        auto *w=oRoot()->property("writer").value<QObject *>();
        if (!w) return "{}";
        if (watchedWriter) disconnect(watchedWriter,nullptr,this,nullptr);
        watchedWriter=w;
        connect(w,SIGNAL(lastWriteChanged()),this,SLOT(writerWriteChanged()),Qt::DirectConnection);
        w->setProperty("enabled",true);auto self=qmlEngine(w)->newQObject(w);
        const auto accepted=self.property("begin").callWithInstance(self,{js(observer()->parent()->parent(),"proposal")});
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"accepted",accepted.toBool()},{"state",js(w,"state").toVariant()}}).toJson(QJsonDocument::Compact));
    }
    QString SingleStatus() const { return statusFor(oRoot()->property("writer").value<QObject *>()); }
    QString WriterStart() {
        if (!isolatedWriterContext()) return "{}";
        auto *w=writer(), *o=observer();
        if (!w || !o) return "{}";
        w->setProperty("enabled",true);
        auto *engine=qmlEngine(w);
        auto self=engine->newQObject(w);
        auto accepted=self.property("begin").callWithInstance(self,{js(o->parent()->parent(),"proposal")});
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"accepted",accepted.toBool()},
            {"state",js(w,"state").toVariant()},{"writeCount",w->property("writeCount")}}).toJson(QJsonDocument::Compact));
    }
    QString WriterStatus() const { return statusFor(writer()); }
    QString statusFor(QObject *w) const {
        if (!w) return "{}";
        return QString::fromUtf8(QJsonDocument::fromVariant(QVariantMap{{"state",js(w,"state").toVariant()},
            {"writeCount",w->property("writeCount")},{"undoRecord",js(w,"undoRecord").toVariant()},{"lastWrite",js(w,"lastWrite").toVariant()},
            {"lastFailure",w->property("lastFailure")},{"fault",faultReceipt},{"writeHistory",writeHistory},
            {"desktop",desktopSnapshot()},
            {"warningActive",w->property("warning").value<QObject *>()->property("activeGeneration")}}).toJson(QJsonDocument::Compact));
    }
    QObject *oRoot() const {
        auto *o=observer();return o?o->parent()->parent()->parent():nullptr;
    }
    QVariant desktopSnapshot() const {
        auto *root=oRoot();auto self=qmlEngine(root)->newQObject(root);
        return self.property("capture").callWithInstance(self).toVariant();
    }
    QString Duplicate() {
        auto *o=observer();
        if (!o) return "{}";
        auto *engine=qmlEngine(o);
        QQmlComponent component(engine);
        if (qgetenv("OWG_TEST_PACKAGED_NATIVE")=="1") {
            const QUrl base=QQmlEngine::contextForObject(o)->baseUrl();
            QFile info(base.resolved(QUrl("native/build-info.json")).toLocalFile());
            if (!info.open(QIODevice::ReadOnly)) return "{}";
            const auto hash=QJsonDocument::fromJson(info.readAll()).object().value("pluginSHA256").toString();
            component.setData(("import \"./native/"+hash+"/org/oledwindowguard/Native\" as Local; Local.NativeObserver {}").toUtf8(),base.resolved(QUrl("DuplicateNative.qml")));
        } else component.setData("import org.oledwindowguard.Native 1.0; NativeObserver {}",QUrl());
        auto *duplicate=component.create();
        const bool rejected=duplicate && !duplicate->property("available").toBool();
        delete duplicate;
        return QString::fromUtf8(QJsonDocument(QJsonObject{{"ok",rejected && observer()==o && o->property("available").toBool()}}).toJson(QJsonDocument::Compact));
    }
private:
    QPointer<QObject> passiveVisual;
    bool registered=false;
    QMetaObject::Connection moveAudit;
    QVariantMap moveAuditFrame;
    QPointer<QObject> watchedWriter;
    QString faultKind="none";
    bool faultFired=false;
    QVariantMap faultReceipt;
    QVariantList writeHistory;
    QSet<QString> topologyTracked;
    QPointer<QObject> armedObserver;
    QString physicalKind,physicalOutput;
    QVariantMap physicalBaseline,physicalResult;
    double physicalControllerBefore=0;
    bool physicalHadProposal=false;
};
KWIN_EFFECT_FACTORY_SUPPORTED(CancellationCheck,"check.json",return qgetenv("OWG_NATIVE_CHECK_ISOLATED")=="1";)
#include "check.moc"
