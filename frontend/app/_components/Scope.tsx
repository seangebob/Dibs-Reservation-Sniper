/**
 * Static scope furniture — the grid, the reticle watermark, the corner
 * targeting brackets, and the brand mark. Pure presentational SVG, no state,
 * so these render on the server and never ship JS.
 */

export function ScopeBackdrop() {
  return (
    <>
      <div className="scope-grid" aria-hidden="true" />
      <svg
        className="reticle"
        viewBox="0 0 300 300"
        fill="none"
        stroke="#e9b24d"
        strokeWidth="1"
        aria-hidden="true"
      >
        <circle cx="150" cy="150" r="140" strokeOpacity="0.3" />
        <circle cx="150" cy="150" r="96" strokeOpacity="0.5" />
        <circle cx="150" cy="150" r="52" strokeOpacity="0.75" />
        <line x1="150" y1="4" x2="150" y2="58" />
        <line x1="150" y1="242" x2="150" y2="296" />
        <line x1="4" y1="150" x2="58" y2="150" />
        <line x1="242" y1="150" x2="296" y2="150" />
        <circle cx="150" cy="150" r="4" fill="#e9b24d" stroke="none" />
        <g strokeOpacity="0.6">
          <line x1="150" y1="96" x2="150" y2="108" />
          <line x1="150" y1="192" x2="150" y2="204" />
          <line x1="96" y1="150" x2="108" y2="150" />
          <line x1="192" y1="150" x2="204" y2="150" />
        </g>
      </svg>
      <svg
        className="corner corner-tl"
        fill="none"
        stroke="#e9b24d"
        strokeWidth="1.5"
        strokeOpacity="0.7"
        aria-hidden="true"
      >
        <path d="M1 10 V1 H10" />
      </svg>
      <svg
        className="corner corner-br"
        fill="none"
        stroke="#e9b24d"
        strokeWidth="1.5"
        strokeOpacity="0.7"
        aria-hidden="true"
      >
        <path d="M27 18 V27 H18" />
      </svg>
    </>
  );
}

export function ScopeMark() {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#e9b24d"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="8.5" />
      <line x1="12" y1="1.5" x2="12" y2="5" />
      <line x1="12" y1="19" x2="12" y2="22.5" />
      <line x1="1.5" y1="12" x2="5" y2="12" />
      <line x1="19" y1="12" x2="22.5" y2="12" />
      <circle cx="12" cy="12" r="2" fill="#e9b24d" stroke="none" />
    </svg>
  );
}
