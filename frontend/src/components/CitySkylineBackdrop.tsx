// Placeholder skyline (approximates the reference mockup's photo) — swap for real brand photography.
export function CitySkylineBackdrop() {
  return (
    <svg
      className="absolute inset-0 h-full w-full opacity-40"
      viewBox="0 0 600 800"
      preserveAspectRatio="xMidYMax slice"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <g stroke="#7fa8e8" strokeWidth="1" opacity="0.5">
        {Array.from({ length: 10 }).map((_, i) => (
          <line key={`v${i}`} x1={i * 60} y1="0" x2={i * 60} y2="800" />
        ))}
        {Array.from({ length: 14 }).map((_, i) => (
          <line key={`h${i}`} x1="0" y1={i * 60} x2="600" y2={i * 60} />
        ))}
      </g>
      <g fill="#0a1626" opacity="0.55">
        <rect x="20" y="520" width="60" height="280" />
        <rect x="90" y="440" width="50" height="360" />
        <rect x="150" y="580" width="45" height="220" />
        <rect x="205" y="360" width="55" height="440" />
        <rect x="270" y="470" width="40" height="330" />
        <rect x="320" y="260" width="35" height="540" />
        <rect x="365" y="500" width="60" height="300" />
        <rect x="435" y="400" width="45" height="400" />
        <rect x="490" y="560" width="50" height="240" />
        <rect x="550" y="460" width="40" height="340" />
      </g>
    </svg>
  );
}
