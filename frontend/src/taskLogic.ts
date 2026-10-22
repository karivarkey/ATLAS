// Pure logic for tasks: recurrence dates + reminder scoring.
// Mirrors backend/app/services/tasks.py. Tested in taskLogic.test.ts — no DOM.

export type Recurrence = 'daily' | 'weekly' | 'monthly' | 'weekdays' | `interval:${number}` | ''

export function validateRecurrence(rec: string): void {
  if (['daily', 'weekly', 'monthly', 'weekdays', ''].includes(rec)) return
  const m = /^interval:(\d+)$/.exec(rec)
  if (m && Number(m[1]) >= 1 && Number(m[1]) <= 365) return
  throw new Error(`unknown recurrence: ${rec}`)
}

function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
}

function matchesRule(day: Date, rec: string, anchor: Date): boolean {
  if (rec === 'daily') return true
  if (rec === 'weekdays') return day.getUTCDay() !== 0 && day.getUTCDay() !== 6
  if (rec === 'weekly') return day.getUTCDay() === anchor.getUTCDay()
  if (rec === 'monthly') {
    return day.getUTCDate() === Math.min(anchor.getUTCDate(),
      daysInMonth(day.getUTCFullYear(), day.getUTCMonth()))
  }
  const m = /^interval:(\d+)$/.exec(rec)
  if (m) {
    const diffDays = Math.round(
      (Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate()) -
        Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), anchor.getUTCDate())) / 86400000)
    return diffDays % Number(m[1]) === 0
  }
  return false
}

/** Occurrence midnights (UTC ms) strictly after startMs, capped at count. */
export function occurrenceDates(
  recurrence: string, anchorMs: number, startMs: number, count = 30,
): number[] {
  validateRecurrence(recurrence)
  if (!recurrence) return []
  const anchor = new Date(anchorMs || startMs)
  const out: number[] = []
  let day = new Date(startMs)
  let guard = 0
  while (out.length < count && guard < 1500) {
    guard++
    day = new Date(day.getTime() + 86400000)
    const d = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate()))
    if (matchesRule(d, recurrence, anchor)) out.push(d.getTime())
  }
  return out
}

export interface ScoredTask {
  id: string
  status: string
  priority: 'critical' | 'high' | 'normal' | 'low'
  due_at: number
  snoozedUntil?: number
  dismissed?: boolean
}

const WEIGHT: Record<string, number> = { critical: 4, high: 3, normal: 2, low: 1 }

export function inQuietHours(nowMs: number, start = 22, end = 7, hour?: number): boolean {
  const h = hour ?? new Date(nowMs).getHours()
  if (start <= end) return start <= h && h < end
  return h >= start || h < end
}

/** importance × deadline proximity; 0 when snoozed/dismissed/quiet/terminal. */
export function reminderScore(
  task: ScoredTask, nowMs: number, opts: { quiet?: boolean } = {},
): number {
  if (['completed', 'cancelled', 'archived'].includes(task.status)) return 0
  if (task.dismissed) return 0
  if ((task.snoozedUntil ?? 0) > nowMs) return 0
  if (opts.quiet ?? inQuietHours(nowMs)) return 0
  const importance = WEIGHT[task.priority] ?? 2
  let proximity: number
  if (!task.due_at) proximity = 0.25
  else {
    const hoursLeft = (task.due_at - nowMs) / 3600000
    if (hoursLeft < 0) proximity = 3 + Math.min(2, Math.abs(hoursLeft) / 48)
    else if (hoursLeft < 2) proximity = 2.5
    else if (hoursLeft < 24) proximity = 1.5
    else if (hoursLeft < 72) proximity = 1
    else proximity = 0.5
  }
  return Math.round(importance * proximity * 1000) / 1000
}

/** Filter bucket for the task list. */
export function taskBucket(
  t: { status: string; due_at: number }, nowMs: number,
): 'today' | 'overdue' | 'waiting' | 'inbox' | 'all' {
  const dayStart = nowMs - (nowMs % 86400000)
  if (t.status === 'waiting_input' || t.status === 'waiting_approval') return 'waiting'
  if (t.status === 'inbox') return 'inbox'
  if (t.due_at && t.due_at < nowMs && !['completed', 'cancelled', 'archived'].includes(t.status))
    return 'overdue'
  if (t.due_at && t.due_at < dayStart + 86400000 &&
    !['completed', 'cancelled', 'archived'].includes(t.status)) return 'today'
  return 'all'
}
