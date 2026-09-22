import type { Role } from "./types";

export const DIVISIONS = {
  estimation: { label: "Estimation", role: "estimation_engineer" },
  "fire-fighting": { label: "Fire Fighting", role: "fire_fighting_engineer" },
  elv: { label: "ELV", role: "elv_engineer" },
} as const;
export type Division = keyof typeof DIVISIONS;
export function divisionForRole(role?: Role): Division | undefined {
  return (Object.keys(DIVISIONS) as Division[]).find(key => DIVISIONS[key].role === role);
}
