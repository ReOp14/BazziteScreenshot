// Loaded temporarily by the BazziteScreenshot daemon. Reports the geometry of
// every visible application window (bottom-to-top stacking order) and the
// cursor position back to the daemon over D-Bus, then the daemon unloads this
// script again.
(function () {
    const result = [];
    const windows = workspace.stackingOrder;
    const desktop = workspace.currentDesktop;
    const activity = workspace.currentActivity;

    for (let i = 0; i < windows.length; i++) {
        const w = windows[i];
        if (!w || w.deleted || !(w.normalWindow || w.dialog)) {
            continue;
        }
        if (w.minimized || w.hidden || w.skipTaskbar || w.opacity === 0) {
            continue;
        }
        if (!w.onAllDesktops) {
            const desktops = w.desktops || [];
            let onCurrent = false;
            for (let j = 0; j < desktops.length; j++) {
                if (desktops[j] && desktop && desktops[j].id === desktop.id) {
                    onCurrent = true;
                }
            }
            if (!onCurrent) {
                continue;
            }
        }
        const activities = w.activities || [];
        if (activities.length > 0 && activities.indexOf(activity) < 0) {
            continue;
        }
        const g = w.frameGeometry;
        result.push({
            caption: String(w.caption || ""),
            resourceClass: String(w.resourceClass || ""),
            pid: w.pid || 0,
            x: g.x,
            y: g.y,
            width: g.width,
            height: g.height,
            z: i,
        });
    }

    const cursor = workspace.cursorPos;
    callDBus("io.github.bazzitescreenshot", "/", "io.github.bazzitescreenshot", "WindowList",
             JSON.stringify({cursor: {x: cursor.x, y: cursor.y}, windows: result}));
})();
