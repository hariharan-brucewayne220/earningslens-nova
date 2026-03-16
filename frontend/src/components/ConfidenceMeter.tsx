import type { ClaimsResponse } from '../types';

interface ConfidenceMeterProps {
  stats: Pick<ClaimsResponse, 'total' | 'verified' | 'flagged' | 'unverifiable'>;
}

function SegmentBar({ stats }: { stats: ConfidenceMeterProps['stats'] }) {
  if (stats.total === 0) {
    return (
      <div className="h-1.5 rounded-full bg-white/5 overflow-hidden w-full" />
    );
  }
  const vPct = (stats.verified / stats.total) * 100;
  const fPct = (stats.flagged / stats.total) * 100;
  const uPct = (stats.unverifiable / stats.total) * 100;

  return (
    <div className="h-1.5 rounded-full overflow-hidden w-full flex gap-0.5">
      {vPct > 0 && (
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${vPct}%`, background: 'linear-gradient(90deg, #059669, #10b981)' }}
        />
      )}
      {fPct > 0 && (
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${fPct}%`, background: 'linear-gradient(90deg, #e11d48, #f43f5e)' }}
        />
      )}
      {uPct > 0 && (
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${uPct}%`, background: 'linear-gradient(90deg, #d97706, #f59e0b)' }}
        />
      )}
    </div>
  );
}

export function ConfidenceMeter({ stats }: ConfidenceMeterProps) {
  const verifiedPct = stats.total > 0 ? Math.round((stats.verified / stats.total) * 100) : 0;

  return (
    <div className="flex items-center gap-5">
      {/* Score */}
      <div className="flex items-baseline gap-1.5 shrink-0">
        <span
          className="text-2xl font-mono font-bold"
          style={{
            color: verifiedPct >= 70 ? '#10b981' : verifiedPct >= 40 ? '#f59e0b' : '#f43f5e',
          }}
        >
          {verifiedPct}%
        </span>
        <span className="section-label">verified</span>
      </div>

      {/* Segment bar + counts */}
      <div className="flex-1 flex flex-col gap-1.5">
        <SegmentBar stats={stats} />
        <div className="flex items-center gap-4">
          <Stat value={stats.verified} label="Verified" color="#10b981" />
          <Stat value={stats.flagged} label="Flagged" color="#f43f5e" />
          <Stat value={stats.unverifiable} label="Uncertain" color="#f59e0b" />
          <span className="ml-auto section-label">{stats.total} total</span>
        </div>
      </div>
    </div>
  );
}

function Stat({ value, label, color }: { value: number; label: string; color: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-sm font-mono font-semibold" style={{ color }}>{value}</span>
      <span className="section-label">{label}</span>
    </div>
  );
}
