import type { Claim } from '../types';

interface VerificationFeedProps {
  claims: Claim[];
}

const VERDICT_CONFIG = {
  VERIFIED: {
    badge: 'bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30',
    borderClass: 'border-verified',
    dot: 'bg-emerald-500',
    label: 'Verified',
  },
  FLAGGED: {
    badge: 'bg-rose-500/15 text-rose-400 ring-1 ring-rose-500/30',
    borderClass: 'border-flagged',
    dot: 'bg-rose-500',
    label: 'Flagged',
  },
  UNVERIFIABLE: {
    badge: 'bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30',
    borderClass: 'border-unverifiable',
    dot: 'bg-amber-500',
    label: 'Unverifiable',
  },
} as const;

function ConfidenceBar({ pct, verdict }: { pct: number; verdict: Claim['verdict'] }) {
  const color =
    verdict === 'VERIFIED' ? '#10b981' :
    verdict === 'FLAGGED'  ? '#f43f5e' : '#f59e0b';
  return (
    <div className="confidence-bar flex-1">
      <div
        className="confidence-bar-fill"
        style={{ width: `${Math.round(pct)}%`, background: color }}
      />
    </div>
  );
}

function ClaimCard({ claim, isNew }: { claim: Claim; isNew: boolean }) {
  const cfg = VERDICT_CONFIG[claim.verdict];
  const pct = Math.round(claim.confidence * 100);
  const filingMatchText =
    typeof claim.filing_match === 'string'
      ? claim.filing_match
      : claim.filing_match != null
      ? String(claim.filing_match)
      : '';

  // Deduplicate: don't show filing_match if it's basically the same as explanation
  const showFilingMatch =
    filingMatchText &&
    filingMatchText.trim().length > 10 &&
    claim.explanation &&
    filingMatchText.trim().toLowerCase().slice(0, 40) !==
      claim.explanation.trim().toLowerCase().slice(0, 40);

  return (
    <div
      className={`glass-card rounded-xl overflow-hidden transition-all duration-200 ${cfg.borderClass} ${isNew ? 'claim-card-enter' : ''}`}
    >
      {/* Header row */}
      <div className="px-4 pt-3 pb-2 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 mt-0.5 ${cfg.dot}`} />
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-md ${cfg.badge}`}>
            {cfg.label}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs font-mono text-slate-500">{pct}%</span>
          <ConfidenceBar pct={pct} verdict={claim.verdict} />
        </div>
      </div>

      {/* Claim text */}
      <div className="px-4 pb-2">
        <p className="text-sm text-slate-200 leading-relaxed">
          {claim.claim_text}
        </p>
      </div>

      {/* Metric / Value / Delta row */}
      {(claim.metric || claim.value || claim.filing_delta) && (
        <div className="px-4 pb-3 flex items-center gap-2 flex-wrap">
          {claim.metric && (
            <span className="section-label">{claim.metric}</span>
          )}
          {claim.metric && claim.value && (
            <span className="text-slate-600 text-xs">·</span>
          )}
          {claim.value && (
            <span className="text-xs font-mono text-slate-300 bg-white/5 px-2 py-0.5 rounded">
              {claim.value}
            </span>
          )}
          {claim.filing_delta && (
            <span className={`text-xs font-mono px-2 py-0.5 rounded ${
              claim.verdict === 'FLAGGED'
                ? 'bg-rose-500/15 text-rose-400'
                : 'bg-slate-700/60 text-slate-400'
            }`}>
              Δ {claim.filing_delta}
            </span>
          )}
        </div>
      )}

      {/* Explanation */}
      {claim.explanation && (
        <div className="px-4 pb-3">
          <p className="text-xs text-slate-400 leading-relaxed">
            {claim.explanation}
          </p>
        </div>
      )}

      {/* Filing match quote — only if distinct from explanation */}
      {showFilingMatch && (
        <div className="mx-4 mb-3 px-3 py-2 rounded-lg bg-white/[0.03] border-l-2 border-slate-600">
          <span className="section-label block mb-1">SEC Filing</span>
          <p className="text-xs text-slate-500 leading-relaxed italic">
            "{filingMatchText}"
          </p>
        </div>
      )}
    </div>
  );
}

export function VerificationFeed({ claims }: VerificationFeedProps) {
  const ordered = [...claims].reverse();

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Panel header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.06] shrink-0">
        <h2 className="section-label">Verification Feed</h2>
        {claims.length > 0 && (
          <span
            className="ml-auto text-xs font-mono"
            style={{ color: 'rgba(148,163,184,0.4)' }}
          >
            {claims.length} claim{claims.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-2.5">
        {ordered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 opacity-40">
            <div className="w-8 h-8 rounded-full border border-slate-700 flex items-center justify-center">
              <div className="w-2 h-2 rounded-full bg-slate-600 pulse-dot" />
            </div>
            <p className="text-xs text-slate-500">Awaiting claims…</p>
          </div>
        ) : (
          ordered.map((claim, idx) => (
            <ClaimCard
              key={`${(claim.claim_text ?? '').slice(0, 30)}-${idx}`}
              claim={claim}
              isNew={idx === 0}
            />
          ))
        )}
      </div>
    </div>
  );
}
