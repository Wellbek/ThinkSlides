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

export type SlideContentItem =
  | { type: "text"; placeholderIdx: number; runs: TextRun[] }
  | { type: "image"; placeholderIdx?: number; image: ImageContent }
  | { type: "table"; placeholderIdx?: number; table: TableContent }
  | { type: "chart"; placeholderIdx?: number; chart: ChartContent };

export interface Slide {
  slideIndex: number;
  layoutId: string;
  layoutName: string;
  contents: SlideContentItem[];
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