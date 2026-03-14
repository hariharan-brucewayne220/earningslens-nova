import type { ClaimsResponse } from '../types';

interface ConfidenceMeterProps {
  stats: Pick<ClaimsResponse, 'total' | 'verified' | 'flagged' | 'unverifiable'>;
}

function CircularProgress({ pct }: { pct: number }) {
  const r = 28;
  const circ = 2 * Math.PI * r;
  const offset = circ - (pct / 100) * circ;

  const color = pct >= 70 ? '#22c55e' : pct >= 40 ? '#eab308' : '#ef4444';

  return (
    <svg width="72" height="72" className="shrink-0">
      <circle
        cx="36"
        cy="36"
        r={r}
        fill="none"
        stroke="#1f2937"
        strokeWidth="6"
      />
      <circle
        cx="36"
        cy="36"
        r={r}
        fill="none"
        stroke={color}
        strokeWidth="6"
        strokeDasharray={circ}
        strokeDashoffset={offset}
        strokeLinecap="round"
        transform="rotate(-90 36 36)"
        style={{ transition: 'stroke-dashoffset 0.5s ease, stroke 0.5s ease' }}
      />
      <text
        x="36"
        y="36"
        textAnchor="middle"
        dominantBaseline="central"
        fill={color}
        fontSize="12"
        fontFamily="ui-monospace, monospace"
        fontWeight="600"
      >
        {Math.round(pct)}%
      </text>
    </svg>
  );
}

export function ConfidenceMeter({ stats }: ConfidenceMeterProps) {
  const pct = stats.total > 0 ? (stats.verified / stats.total) * 100 : 0;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl px-5 py-4">
      <div className="flex items-center gap-5">
        <CircularProgress pct={pct} />
        <div className="flex-1">
          <p className="text-xs text-gray-500 uppercase tracking-widest mb-2">
            Verification Score
          </p>
          <div className="flex items-center gap-4 flex-wrap">
            <Stat label="Verified" value={stats.verified} color="text-green-500" />
            <Stat label="Flagged" value={stats.flagged} color="text-red-500" />
            <Stat label="Unverifiable" value={stats.unverifiable} color="text-yellow-500" />
            <Stat label="Total" value={stats.total} color="text-gray-400" />
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex flex-col">
      <span className={`text-lg font-mono font-semibold ${color}`}>{value}</span>
      <span className="text-xs text-gray-600">{label}</span>
    </div>
  );
}
