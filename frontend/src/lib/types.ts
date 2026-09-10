export type Role = "admin" | "design_manager" | "design_engineer" | "draftsman" | "viewer";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Admin",
  design_manager: "Design Manager",
  design_engineer: "Design Engineer",
  draftsman: "Draftsman",
  viewer: "Viewer",
};
