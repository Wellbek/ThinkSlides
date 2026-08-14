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

/** Where a piece of deck furniture sits. */
export type ChromePosition = "topLeft" | "topRight" | "bottomLeft" | "bottomRight";

export interface ChromeElement {
  enabled?: boolean;
  position?: ChromePosition;
  sizePt?: number;
  color?: string;
  /** Slide roles this element appears on; absent means every role. */
  roles?: string[];
}

/**
 * The deck's furniture, declared rather than hardcoded in the renderer.
 *
 * A design direction distinguishes itself partly by where chrome sits and
 * whether it is there at all, so it has to be a token rather than a constant.
 * A deck declaring none renders the footer band that was always there.
 */
export interface ChromeTokens {
  eyebrow?: ChromeElement & { source?: "sectionName" | "deckTitle" | "none" };
  brandMark?: ChromeElement & { kind?: "text" | "image"; value?: string };
  pageNumber?: ChromeElement;
  pageNumberFormat?: string;
  footer?: ChromeElement;
  footerRule?: ChromeElement;
  /** Roles that carry no chrome at all. Defaults to the cover. */
  suppressedRoles?: string[];
  marginX?: number;
  marginY?: number;
}

/**
 * How a deck DRAWS its charts and tables, not only what colour they are.
 *
 * Colours are the smallest part of why a chart looks like it belongs to a
 * deck. The reference deck's charts are recognisable because they have no
 * value axis and no gridlines, label every bar at its end, and state their
 * unit on a second subtitle line - none of which was recordable before.
 *
 * Every token defaults to what the renderer already drew, so a deck that
 * declares none of them is unchanged.
 */
export interface DataVizTokens {
  chartColorSequence?: string[];
  chartAxisColor?: string;
  chartGridColor?: string;
  fontFamily?: string;

  chartValueAxis?: boolean;
  chartCategoryAxis?: boolean;
  chartValueGridlines?: boolean;
  chartCategoryGridlines?: boolean;
  chartGridlineWeightPt?: number;
  chartBaselineRule?: boolean;

  /** none | all | terminal (last point only) | end (bar ends) | auto */
  chartLabelMode?: "auto" | "none" | "all" | "terminal" | "end";
  chartLabelFormat?: string;
  chartUnitSuffix?: string;

  chartLegend?: "auto" | "none" | "swatches" | "inline";
  /** Where the unit is stated: up the axis, on a second subtitle line, or in the title. */
  chartUnitConvention?: "axis" | "subtitle" | "title";

  /** Fraction of a category slot the bars fill, and the gap between grouped bars. */
  chartBarThickness?: number;
  chartBarGroupGap?: number;

  tableHeaderBg?: string;
  tableHeaderFg?: string;
  tableBandBg?: string;
  tableRowBg?: string;
  tableBorder?: string;
  /** Banded blocks of colour, or rows separated by hairlines on the slide's ground. */
  tableTreatment?: "filled" | "hairline";
  tableRuleWeightPt?: number;
  tableHeaderRuleWeightPt?: number;
  tableHeaderFilled?: boolean;
}

/** How a deck sets its per-slide footnotes and source line. */
export interface ApparatusTokens {
  footnoteSizePt?: number;
  sourceSizePt?: number;
  color?: string;
}

export interface DesignSystemBundle {
  palette: Palette;
  typography: Typography;
  spacing: SpacingTokens;
  chrome?: ChromeTokens;
  apparatus?: ApparatusTokens;
  dataViz?: DataVizTokens;
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

// ──────────────────────────────────────────────
// Primitives - the marks a slide is made of
// ──────────────────────────────────────────────

/**
 * The five marks. Anything drawable on a slide is one of these, so the
 * primitive layer has the same ceiling as writing python-pptx by hand - which
 * is the point. A block (below) carries meaning and compiles down to these; a
 * slide reaches for a primitive directly only when no block is the right
 * shape ("a hairline two thirds of the way across").
 *
 * The escape hatch costs no expressivity and gives up no safety: containment,
 * contrast and measurement all operate at this layer, so a slide that drops
 * to it is checked exactly as thoroughly as one built from blocks.
 */
export type PrimitiveKind = "text" | "rect" | "line" | "ellipse" | "image";

/**
 * One authored mark.
 *
 * Geometry is **fractional against its frame** - the region named by
 * `regionId`, or the whole canvas when there is none - so a composition
 * survives a change of canvas. `id` is stable and addressable: it is what
 * makes "move that heading 4pt left" a patch rather than a redesign.
 */
export interface PrimitiveSpec {
  id?: string;
  kind: PrimitiveKind;
  regionId?: string;

  // Box marks: text, rect, ellipse, image
  x?: number;
  y?: number;
  w?: number;
  h?: number;

  // Line marks
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;

  runs?: TextRun[];
  text?: string;
  sizePt?: number;
  bold?: boolean;
  align?: "left" | "center" | "right" | "justify";
  anchor?: "top" | "middle" | "bottom";
  wrap?: boolean;
  autofit?: boolean;

  fill?: string;
  stroke?: string;
  strokeWeightPt?: number;
  /** Corrected at render if it fails WCAG AA against the ground behind it. */
  color?: string;
  font?: string;

  image?: ImageContent;
  altText?: string;
}

// ──────────────────────────────────────────────
// Block vocabulary
// ──────────────────────────────────────────────

/**
 * A semantic colour, resolved once so a card and the legend explaining it
 * cannot disagree. An explicit "#RRGGBB" is also accepted anywhere a tone is.
 */
export type BlockTone =
  | "benefit" | "risk" | "positive" | "negative" | "neutral" | string;

/** A large figure with a caption underneath. The number is the argument. */
export interface StatCallout {
  value: string;
  caption?: string;
  tone?: BlockTone;
}

/** A circular icon badge over a heading and body copy. */
export interface IconCard {
  icon: string;          // a concept ("financial services"), not a filename
  heading: string;
  body?: string[];
}

/** A card whose left bar carries its meaning - blue benefit, red risk. */
export interface AccentCard {
  heading: string;
  body?: string[];
  tone?: BlockTone;
}

/** One hairline-ruled row: an icon column, a label column, a prose column. */
export interface RowBand {
  icon?: string;
  label: string;
  prose: string;
}

/** An image at a shared height with a ruled title and a caption. */
export interface ImageCard {
  image: ImageContent;
  title: string;
  caption?: string;
}

/** A heading spanning an unequal subset of an image card row. */
export interface ImageCardGroup {
  label: string;
  span: number;
}

/** Explains an encoding used elsewhere on the slide. */
export interface LegendItem {
  label: string;
  tone?: BlockTone;
}

export type SlideContentItem =
  | { type: "text"; placeholderIdx?: number; regionId?: string; runs: TextRun[] }
  | { type: "image"; placeholderIdx?: number; regionId?: string; image: ImageContent }
  | { type: "table"; placeholderIdx?: number; regionId?: string; table: TableContent }
  | { type: "chart"; placeholderIdx?: number; regionId?: string; chart: ChartContent }
  | { type: "statCallout"; regionId?: string; statCallouts: StatCallout[] }
  | { type: "iconCard"; regionId?: string; iconCards: IconCard[] }
  | { type: "accentCard"; regionId?: string; columns?: number; accentCards: AccentCard[] }
  | { type: "rowBand"; regionId?: string; rowBands: RowBand[] }
  | {
      type: "imageCardRow";
      regionId?: string;
      imageCardRow: { cards: ImageCard[]; groups?: ImageCardGroup[] };
    }
  | { type: "banner"; regionId?: string; banner: { text: string } }
  | { type: "legend"; regionId?: string; legend: { items: LegendItem[] } };

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
  /**
   * Which part of the deck this slide belongs to, repeated verbatim across
   * every slide in that part. Printed as the eyebrow, which is what tells a
   * reader where they are in a long document.
   */
  section?: string;
  /**
   * What this slide's headline is for.
   *
   * - `claim` - the slide carries a finding and the headline states it. A
   *   reader who reads only a deck's claim headlines has read its argument.
   * - `question` - the headline asks what the slide answers, repeated in the
   *   same form across comparable slides (the reference deck's chapter
   *   grammar). Rendered exactly as the pattern specifies.
   * - `label` - the headline names the topic. Correct for covers, section
   *   breaks, and any slide with no finding to state.
   *
   * A `claim` is rejected in validation unless the slide carries something to
   * state a finding from - a chart, a table, figures or a comparison.
   */
  titleRegister?: "claim" | "question" | "label";
  /**
   * Footnotes for this slide, numbered per slide and rendered in a block
   * above the source line. A marker is written inline as `[^1]` in any run,
   * table cell or chart label and indexes into this list.
   *
   * Distinct from the deck-wide `sourceRef` trail, which renders as a
   * bracketed "[3]" and resolves on a Sources slide. Both can appear on one
   * slide: the notations differ, so a reader can tell them apart.
   */
  footnotes?: string[];
  /** "Source: McKinsey analysis" - its own element, not the footer band. */
  sourceLine?: string;
  /**
   * Marks the slide places itself, for what no block covers. Drawn behind the
   * slide's blocks, so a tinted panel or a rule frames the content rather
   * than covering it.
   */
  primitives?: PrimitiveSpec[];
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