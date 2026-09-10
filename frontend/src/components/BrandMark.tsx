// Placeholder geometric mark (approximates the reference mockup) — swap for the approved logo asset.
export function BrandMark({ size = 40 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="brandmark-gradient" x1="4" y1="36" x2="36" y2="4" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#1d4ed8" />
          <stop offset="1" stopColor="#60a5fa" />
        </linearGradient>
      </defs>
      <rect x="4" y="20" width="8" height="16" rx="1.5" fill="url(#brandmark-gradient)" opacity="0.85" />
      <rect x="16" y="10" width="8" height="26" rx="1.5" fill="url(#brandmark-gradient)" />
      <rect x="28" y="4" width="8" height="32" rx="1.5" fill="url(#brandmark-gradient)" opacity="0.95" />
    </svg>
  );
}
