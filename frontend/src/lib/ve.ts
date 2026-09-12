import type { VeDesign, VeRack } from "./types";

/** Channels and racks are stored as boundaries -- the zone a channel starts
 * at, the channel a rack starts at -- so moving a channel boundary has to
 * renumber the racks that point past it, or they would silently slide onto
 * a different channel. These keep the two consistent. */

/** Start a new channel at `zone`, splitting the channel that contains it. The
 * new channel inherits that channel's amplifier: it is most often the same
 * amplifier model on the next output. */
export function splitChannelAt(design: VeDesign, zone: number): VeDesign {
  const channels = design.channels;
  if (channels.some((c) => c.first_zone === zone)) return design;
  const at = channels.findIndex((c) => c.first_zone > zone);
  const position = at === -1 ? channels.length : at;
  const parent = channels[position - 1];
  const inserted = {
    first_zone: zone,
    label: null,
    amplifier_watts: parent?.amplifier_watts ?? null,
    sheet_required_watts: null,
  };
  return {
    ...design,
    // The sheet's figure was for the channel before the split.
    channels: [
      ...channels.slice(0, position).map((c, i) =>
        i === position - 1 ? { ...c, sheet_required_watts: null } : c
      ),
      inserted,
      ...channels.slice(position),
    ],
    racks: design.racks.map((r) =>
      r.first_channel >= position ? { ...r, first_channel: r.first_channel + 1 } : r
    ),
  };
}

/** Remove the channel boundary at `zone`, so its zones join the channel
 * before. A rack that started at the removed channel now starts at the one
 * after it; a rack left with no channel is dropped. */
export function mergeChannelAt(design: VeDesign, zone: number): VeDesign {
  const index = design.channels.findIndex((c) => c.first_zone === zone);
  if (index === -1) return design;
  const channels = design.channels
    .filter((_, i) => i !== index)
    .map((c, i) => (i === index - 1 ? { ...c, sheet_required_watts: null } : c));
  const racks: VeRack[] = [];
  for (const rack of design.racks) {
    const first = rack.first_channel > index ? rack.first_channel - 1 : rack.first_channel;
    if (first >= channels.length) continue;
    if (racks.length > 0 && racks[racks.length - 1].first_channel === first) continue;
    racks.push({ ...rack, first_channel: first });
  }
  return { ...design, channels, racks };
}

export function formatWatts(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Number.isInteger(value) ? value : value.toFixed(2).replace(/0$/, "")} W`;
}

export function formatPercent(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined) return "—";
  return `${Math.round(fraction * 100)}%`;
}
