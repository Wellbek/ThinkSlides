"use client";

import { useEffect, useRef, useState } from "react";

export interface ReplaceableAsset {
  id: string;
  filename: string;
  slide_index?: number;
}

export type ReplaceScope = "asset_only" | "in_slide" | "keep_both";

/**
 * Confirmation dialog shown after a file is dropped onto an existing
 * chart/image asset (#187). Three mutually exclusive outcomes, matching the
 * reviewed design: swap just the asset, swap it and also update the slide
 * (routed through the agent, not a plain file write), or keep both.
 */
export function AssetReplaceDialog({
  asset,
  file,
  onConfirm,
  onCancel,
}: {
  asset: ReplaceableAsset;
  file: File;
  onConfirm: (scope: ReplaceScope) => void | Promise<void>;
  onCancel: () => void;
}) {
  const [scope, setScope] = useState<ReplaceScope>("asset_only");
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

  const handleConfirm = async () => {
    setBusy(true);
    try {
      await onConfirm(scope);
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog ref={dialogRef} className="ws-modal" onClose={onCancel}>
      <h4>Replace {asset.filename}?</h4>
      <p className="ws-modal-sub">{file.name} was dropped on this asset.</p>

      <label className="ws-modal-option">
        <input
          type="radio"
          name="replace-scope"
          checked={scope === "asset_only"}
          onChange={() => setScope("asset_only")}
        />
        <div>
          <div className="opt-title">Replace just this asset</div>
          <div className="opt-desc">Swaps the file in the Assets panel only. The slide keeps its current image.</div>
        </div>
      </label>

      <label className="ws-modal-option">
        <input
          type="radio"
          name="replace-scope"
          checked={scope === "in_slide"}
          onChange={() => setScope("in_slide")}
        />
        <div>
          <div className="opt-title">Replace it in the slide too</div>
          <div className="opt-desc">
            {typeof asset.slide_index === "number"
              ? `Also updates Slide ${asset.slide_index + 1} with the new image.`
              : "Also updates the slide that uses this asset."}{" "}
            The agent regenerates that slide.
          </div>
        </div>
      </label>

      <label className="ws-modal-option">
        <input
          type="radio"
          name="replace-scope"
          checked={scope === "keep_both"}
          onChange={() => setScope("keep_both")}
        />
        <div>
          <div className="opt-title">Keep both</div>
          <div className="opt-desc">Adds the dropped file as a new asset instead of overwriting. Slide unaffected.</div>
        </div>
      </label>

      <div className="ws-modal-actions">
        <button type="button" className="ws-btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="ws-btn primary" onClick={handleConfirm} disabled={busy}>
          {busy ? "Replacing..." : "Replace"}
        </button>
      </div>
    </dialog>
  );
}
