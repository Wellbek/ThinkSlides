// components/layout/ResizeHandle.tsx
"use client";

import { useCallback, useRef } from "react";

interface ResizeHandleProps {
  onResizeStart: (e: React.MouseEvent) => void;
  onResizeMove: (delta: number) => void;
  className?: string;
}

export function ResizeHandle({ onResizeStart, onResizeMove, className = "" }: ResizeHandleProps) {
  const startXRef = useRef(0);
  const rafRef = useRef<number>();

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      startXRef.current = e.clientX;
      onResizeStart(e);

      const handleMove = (moveEvent: MouseEvent) => {
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
        rafRef.current = requestAnimationFrame(() => {
          const delta = moveEvent.clientX - startXRef.current;
          if (delta !== 0) {
            onResizeMove(delta);
            startXRef.current = moveEvent.clientX;
          }
        });
      };

      const handleUp = () => {
        document.removeEventListener("mousemove", handleMove);
        document.removeEventListener("mouseup", handleUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        document.body.style.pointerEvents = "";
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
      };

      document.addEventListener("mousemove", handleMove);
      document.addEventListener("mouseup", handleUp);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.body.style.pointerEvents = "none";
    },
    [onResizeStart, onResizeMove]
  );

  return (
    <div
      onMouseDown={handleMouseDown}
      className={`relative flex-shrink-0 cursor-col-resize group z-10 ${className}`}
      style={{ width: "4px", marginLeft: "-2px", marginRight: "-2px" }}
    >
      <div className="absolute inset-y-0 -left-1 -right-1" />
      <div className="absolute inset-y-0 left-[1px] right-[1px] bg-transparent group-hover:bg-ice/30 group-active:bg-ice/50 transition-colors duration-150" />
    </div>
  );
}