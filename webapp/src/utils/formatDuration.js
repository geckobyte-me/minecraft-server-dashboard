/**
 * Format a duration given in seconds into a compact human string.
 * Examples: 45 -> "45s", 3720 -> "1h 2m", 90061 -> "1d 1h"
 */
export function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds || 0))
  if (s < 60) return `${s}s`

  const days = Math.floor(s / 86400)
  const hours = Math.floor((s % 86400) / 3600)
  const minutes = Math.floor((s % 3600) / 60)

  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

/**
 * Format total running seconds as hours with one decimal (for window totals).
 * Example: 9000 -> "2.5h"
 */
export function formatHours(totalSeconds) {
  const hours = (Math.max(0, totalSeconds || 0) / 3600)
  return `${hours.toFixed(1)}h`
}

/**
 * Format a Unix epoch (seconds) as a short local date-time string.
 */
export function formatEpoch(epochSeconds) {
  if (!epochSeconds) return '-'
  const d = new Date(epochSeconds * 1000)
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })
}
