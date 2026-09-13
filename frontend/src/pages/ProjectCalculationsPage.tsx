import { NavLink, Outlet } from "react-router-dom";
import { useProject } from "./ProjectWorkspace";

/** The project's design calculations, one tab per system. Each tab is its
 * own route, so a calculation can be linked to; what is not built yet is
 * listed here too and says so when opened. */
const CALCULATIONS = [
  { to: "battery", label: "Battery" },
  { to: "amplifier", label: "Amplifier", soon: true },
  { to: "power", label: "Power", soon: true },
];

export function ProjectCalculationsPage() {
  // The workspace passes the project through its outlet; this section is a
  // second outlet inside it, and has to hand the same context on -- without
  // it the calculations have no project and render nothing.
  const context = useProject();

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {CALCULATIONS.map((calculation) => (
          <NavLink
            key={calculation.to}
            to={calculation.to}
            className={({ isActive }) =>
              `flex items-center gap-2 rounded-xl border px-5 py-2.5 text-sm font-semibold ${
                isActive
                  ? "border-brand-600 bg-brand-600 text-white"
                  : "border-gray-200 bg-white text-navy-900 hover:border-brand-300"
              }`
            }
          >
            {({ isActive }: { isActive: boolean }) => (
              <>
                {calculation.label}
                {calculation.soon && (
                  <span
                    className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                      isActive ? "bg-white/20 text-white" : "bg-amber-50 text-amber-700"
                    }`}
                  >
                    soon
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </div>

      <div className="mt-5">
        <Outlet context={context} />
      </div>
    </div>
  );
}
