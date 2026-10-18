import QtQuick
import org.kde.kwin

// Test-only placement of three windows belonging to the disposable client.
// Runs before the production observer establishes its geometry baseline.
QtObject {
    property bool placed: false
    property Timer setup: Timer {
        interval: 100; running: true; repeat: true
        onTriggered: {
            const outputs=Workspace.screens
            if (outputs.length!==2) return
            const first=outputs[0].geometry, second=outputs[1].geometry
            let found=0, matches=0
            for (const w of Workspace.stackingOrder) {
                if (w.desktopFileName!=="oled-window-guard-runtime-test") continue
                let target=null
                if (w.caption==="OLED Window Guard owned preview candidate")
                    target=Qt.rect(first.x+30,first.y+100,220,128)
                else if (w.caption==="OLED Window Guard disposable probe window")
                    target=Qt.rect(second.x+80,second.y+150,480,268)
                else if (w.caption==="OLED Window Guard timer clock probe")
                    target=Qt.rect(first.x+5,first.y+5,2,30)
                if (!target) continue
                found++
                if (!placed) w.frameGeometry=target
                const r=w.frameGeometry
                if (Math.abs(r.x-target.x)<0.5 && Math.abs(r.y-target.y)<0.5 &&
                        Math.abs(r.width-target.width)<0.5 && Math.abs(r.height-target.height)<0.5) matches++
            }
            if (found===3) placed=true
            if (placed && matches===3) {
                console.warn("HAND_PLANNING_SETUP_READY")
                stop()
            }
        }
    }
}
