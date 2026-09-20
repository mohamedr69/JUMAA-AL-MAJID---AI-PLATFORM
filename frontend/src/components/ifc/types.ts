export type Category = 'fire_alarm' | 'emergency_light' | 'other'
export type Status = 'verified' | 'suggested' | 'unknown' | 'ignored'

export const CATEGORY_LABEL: Record<Category, string> = {
  fire_alarm: 'Fire Alarm',
  emergency_light: 'Emergency Lighting',
  other: 'Other',
}

export interface DeviceType {
  id: number
  code: string
  name: string
  category: Category
  unit: string
  sort_order: number
  is_active: boolean
  symbol_count: number
  /** smoke, heat, call point...: an answer in another family than the symbol's own words is flagged */
  family?: string | null
}

export interface DeviceTypeRef {
  id: number
  code: string
  name: string
  category: Category
  unit: string
}

export interface Occurrence {
  handle: string
  block_name: string
  layer: string
  x: number
  y: number
  rotation: number
  scale: number
  space: string
  label_over: string
  attrs: Record<string, string>
}

export interface Suggestion {
  symbol_id: number
  score: number
  reason: string
  is_ignored: boolean
  device_type: DeviceTypeRef | null
  label: string
  svg: string
}

export interface SymbolGroup {
  signature: string
  label: string
  inner_label: string
  raster_hex: string
  svg: string
  entity_counts: Record<string, number>
  block_names: Record<string, number>
  layers: Record<string, number>
  spaces: Record<string, number>
  count: number
  size: number
  direct_count: number
  hint: number
  status: Status
  symbol_id: number | null
  device_type: DeviceTypeRef | null
  suggestion: Suggestion | null
  /** What the symbol's own words (letters, block name) say it is: a hint, never counted by itself. */
  name_hint: { device_type: DeviceTypeRef; family: string; reason: string } | null
  /** Why its answer disagrees with its own words, when it does. */
  conflict: string | null
  /** What the user is asked about it before the quantities; null when the library knows its exact drawing. */
  review: ReviewKind | null
  /** instances per sheet name, BOQ quantity (plan sheets x floors), and instances on diagram sheets or off-sheet */
  by_sheet: Record<string, number>
  on_plans: number
  boq_qty: number
  not_counted: number
  /** How a verified group was identified: its exact drawing is in the
   *  library, or it closely matches a library symbol with the same letters. */
  match: { kind: 'exact' | 'library' | 'family'; symbol_id: number; score: number; coverage?: number; svg?: string } | null
  occurrences: Occurrence[]
}

export interface Totals {
  fire_alarm: number
  emergency_light: number
  other: number
  verified_symbols: number
  suggested_symbols: number
  suggested_instances: number
  unknown_symbols: number
  unknown_instances: number
  ignored_symbols: number
  not_counted_instances: number
}

export interface Drawing {
  id: number
  filename: string
  uploaded_at: string
  units: string
  dxf_version: string
  seconds: number
  containers: string[]
  skipped_empty_blocks: string[]
  layouts: string[]
  conversion: { source_format: 'dwg'; converter: string; seconds: number } | null
  sheets: SheetInfo[]
  single_floor: boolean
  floor_info: FloorInfo
  floor_boq: Record<Category, CategoryBoq>
  review: ReviewInfo
  groups: SymbolGroup[]
  totals: Totals
  /** Where the drawing as uploaded was filed in the project's folder (03- Drawings/IFC/Electrical/FA/...). */
  archive_path: string | null
  /** Why it was not filed, when it was not. */
  filed_note: string | null
  /** The IFC revision it was issued as ("R0", "R1" ...). */
  revision: string
  supersedes_id: number | null
  /** The later revision that replaced it; null for the drawing in force. */
  superseded_by: { id: number; revision: string } | null
  /** What was kept from the revision before it. */
  carried_over: { from: string; floor_overrides: number; review_skipped: number } | null
}

export interface DrawingSummary {
  id: number
  filename: string
  uploaded_at: string
  units: string
  seconds: number
  symbol_groups: number
  totals: Totals
  review_required: number
  archive_path: string | null
  revision: string
  supersedes_id: number | null
  superseded_by: number | null
  /** Nothing supersedes it: the drawing in force. */
  current: boolean
  /** Fire alarm building quantities by device code, once verified. */
  devices: Record<string, number>
}

/** BOQ Floor Wise beside BOQ as per IFC Drawings (GET /projects/{id}/ifc-comparison). */
export interface ComparisonFloorCell {
  floor: string
  schedule: number
  ifc: number
  difference: number
}

export interface ComparisonDevice {
  device: string
  schedule_lines: { description: string; catalog_no: string | null; total: number }[]
  ifc_types: { code: string; name: string }[]
  schedule_total: number
  ifc_total: number
  ifc_unplaced: number
  difference: number
  floors_differing: number
  floors: ComparisonFloorCell[]
}

export interface ComparisonFloor {
  key: string
  label: string
  schedule_names: string[]
  ifc_names: string[]
  schedule: number
  ifc: number
  difference: number
  devices_differing: number
}

export interface Comparison {
  devices: ComparisonDevice[]
  floors: ComparisonFloor[]
  unplaced: { drawing: string; revision: string; sheet: string; floor_name: string; multiplier: number; qty: number; floors_read: number[] }[]
  schedule_unnamed: { description: string; total: number }[]
  /** One device's surplus mirroring another's shortfall on every floor: one symbol answered as the other. */
  swaps: { more: string; fewer: string; qty: number; floors: number; ifc_types: string[] }[]
  totals: {
    schedule: number
    ifc: number
    difference: number
    devices: number
    devices_matching: number
    floors: number
    floors_matching: number
  }
  schedule: { file: string; sheet: string; stated_grand_total: number | null } | null
  schedule_note: string | null
  drawings: { id: number; filename: string; revision: string; uploaded_at: string }[]
  pending: { id: number; filename: string; revision: string; uploaded_at: string; review_required: number }[]
}

export interface LibrarySymbol {
  id: number
  signature: string
  label: string
  inner_label: string
  svg: string
  entity_counts: Record<string, number>
  block_names: string[]
  device_type: DeviceTypeRef | null
  is_ignored: boolean
  notes: string
  source_drawing: string
  created_at: string
  updated_at: string
}

export interface Capabilities {
  dxf: boolean
  dwg: boolean
  dwg_converter: string | null
}

export interface SheetInfo {
  name: string
  title: string
  kind: 'plan' | 'diagram' | 'outside'
  floors: number[]
  multiplier: number
  parsed_multiplier?: number
  overridden?: boolean
  note: string
  floor_name: string
}

/** One floor, or several: read from the sheets' title blocks. */
export interface FloorInfo {
  mode: 'single' | 'multiple'
  plans: number
  floors: number
  floor_name: string | null
  excluded_sheets: string[]
  architecture_found: boolean
}

/** Quantities floor by floor: every floor plan in sheet order with its
 *  devices (count on the plan x the floors it stands for), and the
 *  building total per device. The Excel export lists the same. */
export interface FloorBoqRow {
  device_type: DeviceTypeRef
  per_floor: number
  qty: number
}

export interface FloorBoqFloor {
  sheet: string
  title: string
  floor_name: string
  floors: number[]
  multiplier: number
  rows: FloorBoqRow[]
  per_floor: number
  qty: number
}

export interface CategoryBoq {
  floors: FloorBoqFloor[]
  building: { device_type: DeviceTypeRef; qty: number }[]
  qty: number
}

/** The device type list with t added (or replaced), in list order. */
export function withType(types: DeviceType[], t: DeviceType): DeviceType[] {
  return [...types.filter((x) => x.id !== t.id), t].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name))
}

/** Verify first, then quantities. What the user is asked about a symbol:
 *  answer (no guess), suggested (the app's guess to check), architecture
 *  (only on xref layers: not a device, to check), confirm (counted by
 *  resemblance to a library symbol). Not asked: optional (off the floor
 *  plans, or unlikely to be a device) and skipped (left out by the user). */
export type ReviewKind = 'answer' | 'suggested' | 'architecture' | 'confirm' | 'optional' | 'skipped'

export interface ReviewInfo {
  answer: number
  suggested: number
  architecture: number
  confirm: number
  optional: number
  skipped: number
  /** symbols still to answer before the quantities are given */
  required: number
  ready: boolean
  /** answered symbols on the floor plans whose device type disagrees with their own words */
  conflicts: number
}

/** One answer: a device type, not a device, or skip. None of them takes back a skip. */
export interface ReviewAnswer {
  signature: string
  device_type_id?: number
  ignore?: boolean
  skip?: boolean
}
