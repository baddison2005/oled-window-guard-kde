#include <kwin/effect/effect.h>
#include <kwin/effect/effecthandler.h>
#include <kwin/input.h>
#include <kwin/input_event.h>
#include <kwin/input_event_spy.h>
#include <kwin/pointer_input.h>
#include <kwin/keyboard_input.h>
#include <kwin/core/inputdevice.h>
#include <kwin/core/outputbackend.h>
#include <kwin/core/output.h>
#include <kwin/core/session.h>
#include <kwin/main.h>
#include <kwin/workspace.h>
#include <kwin/window.h>
#include <QDebug>
#include <QDBusConnection>
#include <QElapsedTimer>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMap>
#include <QSet>
#include <QTimer>
#include <QUuid>
#include <chrono>

class ObserverEffect;
class ObserverSpy final : public KWin::InputEventSpy {
public:
    explicit ObserverSpy(ObserverEffect *effect) : effect(effect) {}
    void pointerMotion(KWin::PointerMotionEvent *event) override;
    void pointerButton(KWin::PointerButtonEvent *event) override;
    void pointerAxis(KWin::PointerAxisEvent *event) override;
    void keyboardKey(KWin::KeyboardKeyEvent *event) override;
private:
    ObserverEffect *effect;
};
class ObserverEffect final : public KWin::Effect {
    Q_OBJECT
    // Physical DRM removal can emit more than eight resets before a coherent poll.
    static constexpr int TopologyTraceCapacity = 64;
public:
    ObserverEffect() : spy(this), epoch(QUuid::createUuid().toString(QUuid::WithoutBraces)) {
        if (qgetenv("OWG_INPUT_PROBE_ISOLATED") != "1") return;
        clock.start();
        lockState = KWin::effects->isScreenLocked() ? "locked" : "unknown";
        QObject::connect(KWin::effects, &KWin::EffectsHandler::screenAboutToLock, this, [this] {
            ++sessionGeneration;
            lockState = "locking";
            invalidateActivity();
            recordLifecycle("locking");
        });
        QObject::connect(KWin::effects, &KWin::EffectsHandler::screenLockingChanged, this, [this](bool locked) {
            ++sessionGeneration;
            // A false notification can arrive while the greeter is starting.
            // Stay fail-closed until a confirmed locked-to-unlocked transition.
            lockState = locked ? "locked" : lockState == "locking" ? "locking" : "unlocked";
            invalidateActivity();
            recordLifecycle(lockState);
        });
        QObject::connect(KWin::workspace(), &KWin::Workspace::outputsChanged, this, [this] {
            ++topologyGeneration;
            invalidateActivity();
            topologyTransitions.append(resetRecord());
            if (topologyTransitions.size() > TopologyTraceCapacity) topologyTransitions.removeAt(0);
        });
        QObject::connect(KWin::workspace(), &KWin::Workspace::windowActivated, this,
                         [this](KWin::Window *) { ++focusGeneration; });
        if (auto *session = KWin::kwinApp()->session()) {
            QObject::connect(session, &KWin::Session::aboutToSleep, this, [this] {
                ++sessionGeneration;
                sleepState = "sleeping";
                invalidateActivity();
                recordLifecycle("sleeping");
            });
            QObject::connect(session, &KWin::Session::awoke, this, [this] {
                ++sessionGeneration;
                sleepState = "awake";
                invalidateActivity();
                recordLifecycle("awake");
            });
            QObject::connect(session, &KWin::Session::activeChanged, this, [this](bool active) {
                ++sessionGeneration;
                sessionActivity = active ? "active" : "inactive";
                invalidateActivity();
                // Capture the reset synchronously; input may arrive before a poll.
                QJsonObject boundary = resetRecord();
                boundary["active"] = active;
                recordLifecycle(active ? "active" : "inactive");
                activityTransitions.append(boundary);
                if (activityTransitions.size() > 8) activityTransitions.removeAt(0);
            });
        }
        registered = QDBusConnection::sessionBus().registerObject(
            "/org/oledwindowguard/InputProbe", "org.oledwindowguard.InputProbe1", this,
            QDBusConnection::ExportAllSlots);
        if (!registered) qWarning() << "OWG_INPUT snapshot registration failed";
        KWin::input()->installInputEventSpy(&spy);
        qInfo() << "OWG_INPUT installed";
        const QByteArray path = qgetenv("OWG_INPUT_TEST_TRIGGER");
        if (!path.isEmpty()) {
            triggerPath = QString::fromLocal8Bit(path);
            QObject::connect(&timer, &QTimer::timeout, this, [this] { pollTrigger(); });
            timer.start(25);
        }
    }
    ~ObserverEffect() override {
        timer.stop();
        if (addedOutput) KWin::kwinApp()->outputBackend()->removeVirtualOutput(addedOutput);
        if (registered) QDBusConnection::sessionBus().unregisterObject("/org/oledwindowguard/InputProbe");
        qInfo() << "OWG_INPUT removed";
    }
    enum class Kind { Pointer, Button, Axis, Key };
    void invalidateActivity() {
        outputCounts.clear();
        buttonKnown = false;
        buttonsDown = false;
        lastActivityMs = -1;
    }
    bool directMode() const { return direct; }
    bool keyboardFocusKnown() const {
        auto *focused = KWin::workspace()->activeWindow();
        return focused && !focused->isDeleted() && !focused->isInternal();
    }
    void record(Kind kind, const QPointF *position = nullptr, Qt::MouseButtons buttons = Qt::NoButton,
                KWin::InputDevice *device = nullptr) {
        if (!clock.isValid()) return;
        if (lockState == "locking" || lockState == "locked" || sleepState == "sleeping" ||
            sessionActivity == "inactive") return;
        if (kind == Kind::Pointer) ++pointerCount;
        else if (kind == Kind::Button) { ++buttonCount; buttonKnown = true; buttonsDown = buttons != Qt::NoButton; }
        else if (kind == Kind::Axis) ++axisCount;
        else if (kind == Kind::Key) ++keyCount;
        if (device && device->inherits("KWin::LibInput::Device")) {
            const QString key = kind == Kind::Pointer ? "pointer" : kind == Kind::Button ? "button"
                : kind == Kind::Axis ? "axis" : "keyboard";
            ++libinputCounts[key];
        }
        ++sequence;
        lastActivityMs = clock.elapsed();
        if (position) {
            auto *output = KWin::workspace()->outputAt(*position);
            if (output && (outputCounts.contains(output->name()) || outputCounts.size() < 16))
                ++outputCounts[output->name()];
        }
        if (kind == Kind::Key) {
            auto *focused = KWin::workspace()->activeWindow();
            const bool focusKnown = keyboardFocusKnown();
            if (!focusKnown) ++unknownFocusKeyCount;
            const auto frame = focusKnown ? focused->frameGeometry() : KWin::RectF();
            bool attributed = false;
            const auto outputs = KWin::workspace()->outputs();
            if (focusKnown && frame.isValid()) {
                for (auto *output : outputs) {
                    const auto bounds = output->geometry();
                    if (frame.x() < bounds.x() + bounds.width() &&
                        bounds.x() < frame.x() + frame.width() &&
                        frame.y() < bounds.y() + bounds.height() &&
                        bounds.y() < frame.y() + frame.height()) {
                        if (outputCounts.contains(output->name()) || outputCounts.size() < 16)
                            ++outputCounts[output->name()];
                        attributed = true;
                    }
                }
            }
            if (!attributed) {
                for (auto *output : outputs) {
                    if (outputCounts.contains(output->name()) || outputCounts.size() < 16)
                        ++outputCounts[output->name()];
                }
            }
        }
    }
public Q_SLOTS:
    QString ActivityTransitions() const {
        return QString::fromUtf8(QJsonDocument(QJsonObject{
            {"schema", 1}, {"epoch", epoch}, {"transitions", activityTransitions},
            {"lifecycleTransitions", lifecycleTransitions}, {"lifecycleTraceCapacity", 16},
            {"topologyTransitions", topologyTransitions}, {"topologyTraceCapacity", TopologyTraceCapacity},
            {"outputGeometries", outputGeometries()}
        }).toJson(QJsonDocument::Compact));
    }
    QString Environment() const {
        auto *backend = KWin::kwinApp()->outputBackend();
        auto *session = KWin::kwinApp()->session();
        QJsonObject counts;
        for (const auto &key : {"pointer", "button", "axis", "keyboard"})
            counts[key] = double(libinputCounts.value(key));
        QJsonObject result{{"schema", 1}, {"outputBackend", backend ? backend->metaObject()->className() : ""},
                           {"sessionBackend", session ? session->metaObject()->className() : ""},
                           {"sessionActive", session && session->isActive()},
                           {"sessionTerminal", session ? int(session->terminal()) : 0},
                           {"directMode", direct}, {"injectionEnabled", !triggerPath.isEmpty()},
                           {"libinput", counts}};
        return QString::fromUtf8(QJsonDocument(result).toJson(QJsonDocument::Compact));
    }
    QString Snapshot() const {
        QJsonObject result{{"schema", 2}, {"epoch", epoch},
                           {"sequence", double(sequence)}, {"topologyGeneration", double(topologyGeneration)},
                           {"sessionGeneration", double(sessionGeneration)}, {"lockState", lockState},
                           {"sleepState", sleepState}, {"sessionActivity", sessionActivity},
                           {"ageMs", lastActivityMs < 0 ? -1 : clock.elapsed() - lastActivityMs},
                           {"pointer", double(pointerCount)}, {"button", double(buttonCount)},
                           {"axis", double(axisCount)}, {"keyboard", double(keyCount)},
                           {"unknownFocusKeyboard", double(unknownFocusKeyCount)},
                           {"focusKnown", keyboardFocusKnown()}, {"focusGeneration", double(focusGeneration)},
                           {"buttonKnown", buttonKnown}, {"buttonsDown", buttonsDown}};
        QJsonArray outputs;
        const auto available = KWin::workspace()->outputs();
        bool complete = !available.empty() && available.size() <= 16;
        QSet<QString> seen;
        for (auto *output : available) {
            const QString name = output->name();
            if (name.isEmpty() || name.size() > 64 || seen.contains(name)) { complete = false; continue; }
            seen.insert(name);
            if (outputs.size() == 16) { complete = false; break; }
            outputs.append(QJsonObject{{"id", name}, {"events", double(outputCounts.value(name))}});
        }
        result["outputIdentityComplete"] = complete;
        result["outputs"] = outputs;
        return QString::fromUtf8(QJsonDocument(result).toJson(QJsonDocument::Compact));
    }
private:
    QJsonArray outputGeometries() const {
        QJsonArray result;
        for (auto *output : KWin::workspace()->outputs()) {
            if (result.size() == 16) break;
            const auto rect = output->geometry();
            result.append(QJsonObject{{"id", output->name()}, {"x", rect.x()}, {"y", rect.y()},
                {"width", rect.width()}, {"height", rect.height()}, {"scale", output->scale()}});
        }
        return result;
    }
    QJsonObject resetRecord() const {
        auto result = QJsonDocument::fromJson(Snapshot().toUtf8()).object();
        result["monotonicMs"] = double(clock.elapsed());
        result["outputGeometries"] = outputGeometries();
        return result;
    }
    void recordLifecycle(const QString &reason) {
        auto record = resetRecord();
        record["reason"] = reason;
        lifecycleTransitions.append(record);
        if (lifecycleTransitions.size() > 16) lifecycleTransitions.removeAt(0);
    }
    void pollTrigger() {
        QFile input(triggerPath);
        if (!input.open(QIODevice::ReadOnly)) return;
        QByteArray command = input.readAll().trimmed();
        input.close();
        if (command.isEmpty()) return;
        if (!input.open(QIODevice::WriteOnly | QIODevice::Truncate)) return;
        input.close();
        if (command == "topology-burst" || command == "topology-overflow") {
            // Real virtual-backend changes in one trigger callback, without intervening polls.
            const int cycles = command == "topology-burst" ? 5 : 33;
            for (int index = 0; index < cycles; ++index) {
                auto *output = KWin::kwinApp()->outputBackend()->createVirtualOutput(
                    "OWG-Probe-Burst", "Isolated observer burst test", QSize(640, 480), 1.0);
                if (!output) {
                    qWarning() << "OWG_INPUT topology burst output unavailable";
                    return;
                }
                KWin::kwinApp()->outputBackend()->removeVirtualOutput(output);
            }
            return;
        }
        if (command == "add-output") {
            if (!addedOutput) addedOutput = KWin::kwinApp()->outputBackend()->createVirtualOutput(
                "OWG-Probe-Output", "Isolated observer topology test", QSize(640, 480), 1.0);
            return;
        }
        if (command == "remove-output") {
            if (addedOutput) KWin::kwinApp()->outputBackend()->removeVirtualOutput(addedOutput);
            addedOutput = nullptr;
            return;
        }
        if (command == "motion-unlock") {
            auto now = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now().time_since_epoch());
            KWin::input()->pointer()->processMotionAbsolute(QPointF(200, 200), now);
            return;
        }
        if (command == "sleep" || command == "wake" || command == "inactive" || command == "active") {
            auto *session = KWin::kwinApp()->session();
            if (!session || !session->inherits("KWin::NoopSession")) {
                qWarning() << "OWG_INPUT session signal trigger requires private NoopSession";
                return;
            }
            if (command == "sleep") session->aboutToSleep();
            else if (command == "wake") session->awoke();
            else session->activeChanged(command == "active");
            return;
        }
        if (command != "click" && command != "click-warning" &&
            command != "outside-click" && command != "outside-key" &&
            command != "sequence" &&
            command != "keyboard-only" &&
            command != "press" && command != "release") return;
        auto now = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now().time_since_epoch());
        auto *pointer = KWin::input()->pointer();
        if (command == "keyboard-only") {
            KWin::input()->keyboard()->processKey(30, KWin::KeyboardKeyState::Pressed, now);
            KWin::input()->keyboard()->processKey(30, KWin::KeyboardKeyState::Released,
                                                  now + std::chrono::microseconds(1000));
            return;
        }
        if (command == "press" || command == "release") {
            pointer->processButton(0x110, command == "press" ? KWin::PointerButtonState::Pressed
                                                              : KWin::PointerButtonState::Released, now);
            return;
        }
        if (command == "outside-click" || command == "outside-key") {
            KWin::Window *owned = nullptr, *warning = nullptr;
            for (auto *window : KWin::workspace()->stackingOrder()) {
                if (!window || window->isDeleted()) continue;
                if (window->caption() == "OWG owned focus probe" && window->pid() > 0)
                    owned = window;
                if (window->caption() == "OLED Window Guard warning" &&
                    window->pid() == -1 && window->resourceClass().isEmpty())
                    warning = window;
            }
            if (!owned || !warning || KWin::workspace()->activeWindow() != owned) {
                qWarning() << "OWG_INPUT outside input requires owned focus and visible warning";
                return;
            }
            const auto frame = owned->frameGeometry();
            const QPointF target(frame.x() + frame.width() / 2,
                                 frame.y() + frame.height() * 0.72);
            if (!frame.contains(target) || warning->frameGeometry().contains(target)) {
                qWarning() << "OWG_INPUT outside input has no safe owned target";
                return;
            }
            if (command == "outside-click") {
                qInfo() << "OWG_INPUT injecting outside click";
                pointer->processMotionAbsolute(target, now);
                pointer->processButton(0x110, KWin::PointerButtonState::Pressed,
                                       now + std::chrono::microseconds(1000));
                pointer->processButton(0x110, KWin::PointerButtonState::Released,
                                       now + std::chrono::microseconds(2000));
                return;
            }
            qInfo() << "OWG_INPUT injecting outside key";
            KWin::input()->keyboard()->processKey(30, KWin::KeyboardKeyState::Pressed,
                                                  now);
            KWin::input()->keyboard()->processKey(30, KWin::KeyboardKeyState::Released,
                                                  now + std::chrono::microseconds(1000));
            return;
        }
        if (command == "click" || command == "click-warning") {
            qInfo() << "OWG_INPUT injecting compositor pointer click";
            QPointF target(565, 102);
            if (command == "click-warning") {
                KWin::Window *warning = nullptr;
                for (auto *window : KWin::workspace()->stackingOrder()) {
                    if (window && window->caption() == "OLED Window Guard warning"
                            && window->pid() == -1 && window->resourceClass().isEmpty()) {
                        warning = window;
                        break;
                    }
                }
                if (!warning) { qWarning() << "OWG_INPUT warning target unavailable"; return; }
                const auto frame = warning->frameGeometry();
                target = QPointF(frame.x() + frame.width() - 80, frame.y() + 90);
            }
            pointer->processMotionAbsolute(target, now);
            pointer->processButton(0x110, KWin::PointerButtonState::Pressed, now + std::chrono::microseconds(1000));
            pointer->processButton(0x110, KWin::PointerButtonState::Released, now + std::chrono::microseconds(2000));
            return;
        }
        qInfo() << "OWG_INPUT injecting test sequence";
        pointer->processMotionAbsolute(QPointF(200, 200), now);
        pointer->processButton(0x110, KWin::PointerButtonState::Pressed, now + std::chrono::microseconds(1000));
        qInfo() << "OWG_INPUT held" << KWin::input()->qtButtonStates();
        pointer->processAxis(KWin::PointerAxis::Vertical, -15, -120,
                             KWin::PointerAxisSource::Wheel, false,
                             now + std::chrono::microseconds(2000));
        KWin::input()->keyboard()->processKey(30, KWin::KeyboardKeyState::Pressed,
                                              now + std::chrono::microseconds(3000));
        KWin::input()->keyboard()->processKey(30, KWin::KeyboardKeyState::Released,
                                              now + std::chrono::microseconds(4000));
        pointer->processButton(0x110, KWin::PointerButtonState::Released,
                               now + std::chrono::microseconds(5000));
        const auto outputs = KWin::workspace()->outputs();
        if (outputs.size() > 1) {
            const auto frame = outputs[1]->geometry();
            pointer->processMotionAbsolute(QPointF(frame.x() + frame.width() / 2,
                                                    frame.y() + frame.height() / 2),
                                           now + std::chrono::microseconds(6000));
        }
    }
    ObserverSpy spy;
    QTimer timer;
    QString triggerPath;
    QString epoch;
    QString lockState = "unknown";
    QString sleepState = "unknown";
    QString sessionActivity = "unknown";
    QElapsedTimer clock;
    QMap<QString, quint64> outputCounts;
    QMap<QString, quint64> libinputCounts;
    QJsonArray activityTransitions;
    QJsonArray lifecycleTransitions;
    QJsonArray topologyTransitions;
    const bool direct = qgetenv("OWG_INPUT_PROBE_DIRECT") == "1";
    quint64 sequence = 0, topologyGeneration = 0, sessionGeneration = 0;
    quint64 focusGeneration = 0, unknownFocusKeyCount = 0;
    quint64 pointerCount = 0, buttonCount = 0, axisCount = 0, keyCount = 0;
    qint64 lastActivityMs = -1;
    bool buttonKnown = false, buttonsDown = false, registered = false;
    KWin::BackendOutput *addedOutput = nullptr;
};
void ObserverSpy::pointerMotion(KWin::PointerMotionEvent *event) {
    auto *output = KWin::workspace()->outputAt(event->position);
    if (!effect->directMode())
        qInfo() << "OWG_INPUT pointer" << event->position << "output" << (output ? output->name() : QString());
    effect->record(ObserverEffect::Kind::Pointer, &event->position, event->buttons, event->device);
}
void ObserverSpy::pointerButton(KWin::PointerButtonEvent *event) {
    if (!effect->directMode()) qInfo() << "OWG_INPUT button" << int(event->state) << event->buttons;
    effect->record(ObserverEffect::Kind::Button, &event->position, event->buttons, event->device);
}
void ObserverSpy::pointerAxis(KWin::PointerAxisEvent *event) {
    if (!effect->directMode()) qInfo() << "OWG_INPUT axis" << event->position << event->delta;
    effect->record(ObserverEffect::Kind::Axis, &event->position, event->buttons, event->device);
}
void ObserverSpy::keyboardKey(KWin::KeyboardKeyEvent *event) {
    if (!effect->directMode()) qInfo() << "OWG_INPUT key" << int(event->state);
    effect->record(ObserverEffect::Kind::Key, nullptr, Qt::NoButton, event->device);
}
KWIN_EFFECT_FACTORY_SUPPORTED(ObserverEffect, "observer.json",
                              return qgetenv("OWG_INPUT_PROBE_ISOLATED") == "1" &&
                                  !(qgetenv("OWG_INPUT_PROBE_DIRECT") == "1" &&
                                    !qgetenv("OWG_INPUT_TEST_TRIGGER").isEmpty());)
#include "observer.moc"
