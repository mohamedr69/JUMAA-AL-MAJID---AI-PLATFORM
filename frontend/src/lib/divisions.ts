import type { Role } from "./types";

export const DIVISIONS = {
  estimation: { label: "Estimation", role: "fire_alarm_estimation_engineer" },
  "fire-fighting": { label: "Fire Fighting", role: "fire_fighting_estimation_engineer" },
  elv: { label: "ELV", role: "elv_estimation_engineer" },
} as const;
export type Division = keyof typeof DIVISIONS;
export function divisionForRole(role?: Role): Division | undefined {
  return (Object.keys(DIVISIONS) as Division[]).find(key => DIVISIONS[key].role === role);
}
