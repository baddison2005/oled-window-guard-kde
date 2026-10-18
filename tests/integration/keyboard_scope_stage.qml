import QtQuick
import org.kde.kwin

QtObject {
    Component.onCompleted: {
        const active = Workspace.activeWindow
        if (!active || active.deleted || active.caption !== "OWG owned keyboard scope probe") {
            print("OWG_KEYBOARD_SCOPE_FAIL __STAGE__ __MODE__ focus-unavailable")
            return
        }
        const screens = Workspace.screens || []
        if (screens.length !== 2) {
            print("OWG_KEYBOARD_SCOPE_FAIL __STAGE__ __MODE__ outputs=" + screens.length)
            return
        }
        const first = screens[0].geometry, second = screens[1].geometry
        let target
        if ("__STAGE__" === "first")
            target = {x: first.x + 100, y: first.y + 100, width: 320, height: 240}
        else if ("__STAGE__" === "second")
            target = {x: second.x + 100, y: second.y + 100, width: 320, height: 240}
        else
            target = {x: first.x + first.width - 160, y: first.y + 100,
                      width: 320, height: 240}
        if ("__MODE__" === "move") {
            active.frameGeometry = target
            print("OWG_KEYBOARD_SCOPE_MOVED __STAGE__ " + JSON.stringify(target))
            return
        }
        const actual = active.frameGeometry
        if (Math.abs(actual.x - target.x) <= 1 && Math.abs(actual.y - target.y) <= 1 &&
                Math.abs(actual.width - target.width) <= 1 &&
                Math.abs(actual.height - target.height) <= 1)
            print("OWG_KEYBOARD_SCOPE_READY __STAGE__ " + JSON.stringify(actual))
        else print("OWG_KEYBOARD_SCOPE_FAIL __STAGE__ readback=" + JSON.stringify(actual) +
                   " expected=" + JSON.stringify(target))
    }
}
