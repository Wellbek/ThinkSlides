"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { NavRail } from "./NavRail";
import { HomeView } from "./HomeView";
import { DetailsPanel } from "./DetailsPanel";
import { SlideStage } from "./SlideStage";
import { ChatFlow } from "./ChatFlow";
import { SetupBanner } from "./SetupBanner";
import { SettingsModal } from "@/components/layout/SettingsModal";
import { Share2, Eye } from "lucide-react";

type View = "home" | "workspace";

interface Panes {
  left: boolean;
  center: boolean;
  right: boolean;
}

const MIN_LEFT = 190;
const MIN_RIGHT = 300;
const MIN_CENTER = 220;

export function WorkspaceShell() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);

  const [view, setView] = useState<View>("home");
  const [navExt, setNavExt] = useState(false);
  const [panes, setPanes] = useState<Panes>({ left: true, center: true, right: true });
  const [leftW, setLeftW] = useState(248);
  const [rightW, setRightW] = useState(392);

  // Keep a ref mirror so drag handlers read fresh values without re-binding.
  const stateRef = useRef({ panes, leftW, rightW });
  stateRef.current = { panes, leftW, rightW };

  // Drop into the workspace when a *different* session becomes active - the
  // signal is a session being picked or created, not the view being on home.
  // Reading `view` here instead would make this an invariant ("a session is
  // active, therefore show the workspace") and the Home button a no-op: it
  // sets the view to home, the effect re-runs with the session still active,
  // and flips it straight back.
  const shownSessionId = useRef<string | null>(activeSessionId);
  useEffect(() => {
    if (activeSessionId && activeSessionId !== shownSessionId.current) {
      setView("workspace");
    }
    shownSessionId.current = activeSessionId;
  }, [activeSessionId]);

  const enterSession = useCallback(() => {
    setNavExt(false);
    setView("workspace");
  }, []);

  // ---- Resize / retract cascade ----
  // One pane flexes (priority center > right > left); the others are fixed.
  // Dragging a boundary grows a pane and, instead of capping, retracts the
  // neighbour it is squeezing (center first, then the opposite side).
  const startDrag = useCallback((kind: "left" | "right") => {
    return (e: React.MouseEvent) => {
      e.preventDefault();
      let lastX = e.clientX;
      const root = document.querySelector(".ws-root") as HTMLElement | null;
      root?.classList.add("dragging");

      const leftEl = document.getElementById("ws-left");
      const centerEl = document.getElementById("ws-center");
      const rightEl = document.getElementById("ws-right");

      const apply = (patch: Partial<Panes>, w?: { left?: number; right?: number }) => {
        setPanes((p) => ({ ...p, ...patch }));
        if (w?.left != null) setLeftW(w.left);
        if (w?.right != null) setRightW(w.right);
      };

      const onMove = (ev: MouseEvent) => {
        const d = ev.clientX - lastX;
        lastX = ev.clientX;
        if (d === 0) return;
        const s = stateRef.current;

        if (kind === "right") {
          // Boundary on the left edge of the right pane.
          if (d < 0) {
            const grow = -d;
            if (s.panes.center) {
              apply({}, { right: s.rightW + grow });
              requestAnimationFrame(() => {
                if (centerEl && centerEl.offsetWidth < MIN_CENTER) apply({ center: false });
              });
            } else if (s.panes.left) {
              apply({}, { left: Math.max(0, s.leftW + d) });
              requestAnimationFrame(() => {
                if (leftEl && leftEl.offsetWidth < MIN_LEFT) apply({ left: false });
              });
            }
          } else {
            if (s.panes.center) {
              apply({}, { right: Math.max(MIN_RIGHT, s.rightW - d) });
            } else if (s.panes.left) {
              apply({}, { left: s.leftW + d });
            }
          }
        } else {
          // Boundary on the right edge of the left pane.
          if (d > 0) {
            const grow = d;
            if (s.panes.center) {
              apply({}, { left: s.leftW + grow });
              requestAnimationFrame(() => {
                if (centerEl && centerEl.offsetWidth < MIN_CENTER) apply({ center: false });
              });
            } else if (s.panes.right) {
              apply({}, { left: s.leftW + grow });
              requestAnimationFrame(() => {
                if (rightEl && rightEl.offsetWidth < MIN_RIGHT) apply({ right: false });
              });
            }
          } else {
            apply({}, { left: Math.max(MIN_LEFT, s.leftW + d) });
          }
        }
      };

      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        root?.classList.remove("dragging");
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    };
  }, []);

  // Determine which pane flexes.
  const flexPane = panes.center ? "center" : panes.right ? "right" : panes.left ? "left" : null;
  const setFlex = (open: boolean, isFlex: boolean, w?: number): React.CSSProperties => {
    if (!open) return { display: "none" };
    const base: React.CSSProperties = { display: "flex", minWidth: 0 };
    if (isFlex) return { ...base, flex: "1 1 0" };
    return { ...base, flex: "0 0 auto", width: w };
  };

  const leftStyle = setFlex(panes.left, flexPane === "left", leftW);
  const centerStyle = setFlex(panes.center, flexPane === "center");
  const rightStyle = setFlex(panes.right, flexPane === "right", rightW);

  const allClosed = !panes.left && !panes.center && !panes.right;
  const showResL = panes.left && panes.center;
  const showResR = panes.center && panes.right;
  const showResM = panes.left && panes.right && !panes.center;

  const activeSession = useChatStore((s) =>
    s.activeSessionId ? s.sessions.find((x) => x.id === s.activeSessionId) ?? null : null
  );

  return (
    <div className="ws-root">
      {/* Above the app row, not inside it: the alert is about the whole
          workspace, so it spans the whole window - nav rail included - and
          takes its height out of everything below rather than floating over
          it. It is also the only place this is said; the composer used to
          repeat it directly above the input, which put the same sentence on
          screen twice. */}
      <SetupBanner />
      <div className="ws-app-row">
        <NavRail
          extended={navExt}
          onToggleExtend={() => setNavExt((v) => !v)}
          view={view}
          onHome={() => setView("home")}
          onPickSession={(id) => {
            useChatStore.getState().setActiveSession(id).finally(() => enterSession());
          }}
          onNewChat={() => { setNavExt(false); setView("home"); }}
        />

        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          {view === "home" || !activeSessionId ? (
            <HomeView onStarted={enterSession} />
          ) : (
            <>
              <header className="ws-topbar">
                <div className="ws-crumb">
                  <span>ThinkSlides</span>
                  <span className="sep">›</span>
                  <span className="here">{activeSession?.title ?? "Session"}</span>
                </div>
                <div className="ws-spacer" />
                {!panes.center && (
                  <button className="ws-tb-btn primary" onClick={() => setPanes((p) => ({ ...p, center: true }))}>
                    <Eye size={13} /> Show preview
                  </button>
                )}
                <button className="ws-tb-btn"><Share2 size={13} /> Share</button>
                <span className="ws-av indigo" title="You">WK</span>
              </header>

              <div className="ws-body">
                {/* left rail when retracted */}
                {!panes.left && (
                  <div className="ws-rail left" onClick={() => setPanes((p) => ({ ...p, left: true }))} title="Show details">
                    <RailChevron dir="right" />
                    <span className="vlabel">Details</span>
                  </div>
                )}

                <section id="ws-left" className="ws-panel left" style={leftStyle}>
                  <button className="ws-collapse abs" onClick={() => setPanes((p) => ({ ...p, left: false }))} title="Collapse details">
                    <RailChevron dir="left" />
                  </button>
                  <DetailsPanel />
                </section>

                {showResL && <div className="ws-resizer" onMouseDown={startDrag("left")} />}

                <section id="ws-center" className="ws-panel center" style={centerStyle}>
                  <SlideStage
                    collapsed={!panes.center}
                    onCollapse={() => setPanes((p) => ({ ...p, center: false }))}
                  />
                </section>

                {allClosed && (
                  <div className="ws-restore">
                    <div className="r-icon"><Eye size={26} /></div>
                    <div className="r-title">All panels hidden</div>
                    <div className="r-sub">Restore the preview, details, or chat to get back to work.</div>
                    <button className="ws-restore-btn" onClick={() => setPanes({ left: true, center: true, right: true })}>
                      Restore workspace
                    </button>
                  </div>
                )}

                {showResR && <div className="ws-resizer" onMouseDown={startDrag("right")} />}
                {showResM && <div className="ws-resizer" onMouseDown={startDrag("right")} />}

                <section id="ws-right" className="ws-panel right" style={rightStyle}>
                  <ChatFlow
                    collapsed={!panes.right}
                    onCollapse={() => setPanes((p) => ({ ...p, right: false }))}
                  />
                </section>

                {!panes.right && (
                  <div className="ws-rail right" onClick={() => setPanes((p) => ({ ...p, right: true }))} title="Show chat">
                    <RailChevron dir="left" />
                    <span className="vlabel">Chat</span>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Rendered once at the shell level so the nav rail's settings button
          works from both the home view and an open session. */}
      <SettingsModal />
    </div>
  );
}

function RailChevron({ dir }: { dir: "left" | "right" }) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      {dir === "left" ? <path d="M15 18l-6-6 6-6" /> : <path d="M9 18l6-6-6-6" />}
    </svg>
  );
}
