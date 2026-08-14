/**
 * Shared Slide JSON Schema
 * This is the canonical schema for slide content passed between
 * the agent loop and the PPTX renderer. The renderer consumes
 * this schema to clone layout XML and fill placeholders.
 */

export interface SlideDimensions {
  widthEmu: number;   // EMU (914400 per inch)
  heightEmu: number;
  widthPx: number;
  heightPx: number;
}

export interface ColorEntry {
  name: string;
  value: string;      // hex like "#1F4E79" or scheme color like "dk1"
  type: "hex" | "scheme" | "system";
}

export interface Palette {
  colors: ColorEntry[];
  schemeName: string;
}

export interface FontSpec {
  typeface: string;
  sizePt: number;
  bold?: boolean;
  italic?: boolean;
  color?: string;
}

export interface Typography {
  titleFont: FontSpec;
  bodyFont: FontSpec;
  captionFont: FontSpec;
  customFonts?: Record<string, FontSpec>;
}

export interface SpacingTokens {
  slidePaddingEmu: number;
  elementGapEmu: number;
  titleTopMarginEmu: number;
  bodyTopMarginEmu: number;
  cornerRadiusEmu?: number;
}

export interface PlaceholderPosition {
  idx: number;         // <p:sp> placeholder idx attribute
  type: "title" | "body" | "subtitle" | "ctrTitle" | "sldNum" | "dt" | "ftr" | "pic" | "chart" | "tbl" | "custom";
  name: string;
  xEmu: number;
  yEmu: number;
  cxEmu: number;
  cyEmu: number;
  rotation?: number;
}

export interface LayoutDef {
  layoutId: string;       // matches rId in slide layout XML
  layoutName: string;
  masterId: string;
  placeholders: PlaceholderPosition[];
  hasBackground: boolean;
  hasDecorations: boolean;
}

export interface MasterElement {
  elementType: "shape" | "image" | "connector";
  name: string;
  position: { xEmu: number; yEmu: number; cxEmu: number; cyEmu: number };
  styleRef?: string;
}

export interface EmbeddedMedia {
  mediaType: "image" | "audio" | "video";
  rId: string;
  fileName: string;
  mimeType: string;
  fileSizeBytes?: number;
}

export interface DesignSystemBundle {
  palette: Palette;
  typography: Typography;
  spacing: SpacingTokens;
  layouts: LayoutDef[];
  slideDimensions: SlideDimensions;
  masterElements: MasterElement[];
  embeddedMedia: EmbeddedMedia[];
}

// ──────────────────────────────────────────────
// Slide Content Schema (agent output → renderer)
// ──────────────────────────────────────────────

export interface TextRun {
  text: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  fontSizePt?: number;
  colorHex?: string;
  fontName?: string;
  bullet?: boolean;
  level?: number;       // indent level 0-8
  /** 1-based index into the deck's reference list, when this run's content
   *  came from a specific web_search finding. Consumed before rendering (see
   *  renderer/citations.py), which turns it into a "[n]" marker and a numbered
   *  entry on the Sources slide - the renderer itself never sees this field. */
  sourceRef?: number;
}

export interface ImageContent {
  source: "url" | "base64" | "embedded";
  data: string;
  altText?: string;
  widthEmu?: number;
  heightEmu?: number;
  placeholderIdx?: number;
}

export interface TableContent {
  rows: string[][];
  headerRow?: boolean;
  placeholderIdx?: number;
}

export interface ChartContent {
  chartType: "bar" | "line" | "pie" | "scatter";
  title?: string;
  categories: string[];
  series: { name: string; values: number[] }[];
  placeholderIdx?: number;
}

// ──────────────────────────────────────────────
// Region tree - a slide's own composition
// ──────────────────────────────────────────────

/**
 * What a region *is*, rather than where it happens to sit. Kinds carry no
 * geometry: they let the template extractor and the block renderers reason
 * about a composition without re-deriving intent from coordinates.
 */
export type RegionKind =
  | "root"      // the whole canvas, when a tree wants an explicit parent
  | "rail"      // a narrow full-height band with its own ground
  | "panel"     // a major area, usually beside a rail or across a split
  | "column"    // one column of a grid
  | "bandRow"   // the container of a set of row bands
  | "band"      // one row band
  | "banner"    // a full-width strip, usually ruled above and below
  | "grid";     // the container of a set of columns or cards

/**
 * One declared area of a slide.
 *
 * Geometry is **fractional against the parent**, never absolute EMU, so a
 * composition written for one canvas survives a change of canvas. A region
 * with no `ground` inherits its parent's.
 */
export interface Region {
  id: string;
  kind: RegionKind;
  x: number;
  y: number;
  w: number;
  h: number;
  ground?: string;
  children?: Region[];
}

export type SlideContentItem =
  | { type: "text"; placeholderIdx?: number; regionId?: string; runs: TextRun[] }
  | { type: "image"; placeholderIdx?: number; regionId?: string; image: ImageContent }
  | { type: "table"; placeholderIdx?: number; regionId?: string; table: TableContent }
  | { type: "chart"; placeholderIdx?: number; regionId?: string; chart: ChartContent };

export interface Slide {
  slideIndex: number;
  layoutId: string;
  layoutName: string;
  contents: SlideContentItem[];
  /**
   * The slide's composition, when it has one. Absent on every slide that
   * binds by placeholder index - which is the degenerate case, and renders
   * through exactly the code it always did.
   */
  regions?: Region[];
  notes?: string;
  speakerNotes?: string;
}

export interface Presentation {
  templateId?: string;
  designSystemBundleId?: string;
  dimensions: SlideDimensions;
  slides: Slide[];
  metadata: {
    title: string;
    author?: string;
    subject?: string;
    createdAt: string;
  };
}