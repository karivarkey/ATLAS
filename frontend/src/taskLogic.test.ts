import { describe, expect, it } from 'vitest'
import { inQuietHours, occurrenceDates, reminderScore, taskBucket, validateRecurrence } from './taskLogic'

const DAY = 86400000
// Monday 2026-10-26 00:00 UTC
const MON = Date.UTC(2026, 9, 26)

describe('recurrence calc', () => {
  it('rejects unknown rules', () => {
    expect(() => validateRecurrence('yearly')).toThrow()
    expect(() => validateRecurrence('interval:0')).toThrow()
    expect(() => occurrenceDates('hourly', MON, MON)).toThrow()
  })

  it('daily yields consecutive days strictly after start', () => {
    const out = occurrenceDates('daily', MON, MON, 3)
    expect(out).toEqual([MON + DAY, MON + 2 * DAY, MON + 3 * DAY])
  })

  it('weekdays skips Sat/Sun', () => {
    const out = occurrenceDates('weekdays', MON, MON - DAY, 6)
    const days = out.map((t) => new Date(t).getUTCDay())
    expect(days).not.toContain(0)
    expect(days).not.toContain(6)
    expect(out).toHaveLength(6)
  })

  it('weekly keeps the anchor weekday', () => {
    const out = occurrenceDates('weekly', MON, MON, 2)
    expect(out.map((t) => new Date(t).getUTCDay())).toEqual([1, 1])
    expect(out[1] - out[0]).toBe(7 * DAY)
  })

  it('interval:N steps N days', () => {
    const out = occurrenceDates('interval:3', MON, MON, 2)
    expect(out[0] - MON).toBe(3 * DAY)
    expect(out[1] - out[0]).toBe(3 * DAY)
  })
})

describe('reminder scoring', () => {
  const base = { id: 't', status: 'planned', priority: 'normal' as const, due_at: 0 }
  const now = MON + 12 * 3600000 // Monday noon

  it('overdue critical outranks future low', () => {
    const a = reminderScore({ ...base, priority: 'critical', due_at: now - 3600000 }, now, { quiet: false })
    const b = reminderScore({ ...base, priority: 'low', due_at: now + 10 * DAY }, now, { quiet: false })
    expect(a).toBeGreaterThan(b)
  })

  it('snoozed / dismissed / terminal / quiet all score 0', () => {
    expect(reminderScore({ ...base, due_at: now - 1, snoozedUntil: now + 1000 }, now, { quiet: false })).toBe(0)
    expect(reminderScore({ ...base, due_at: now - 1, dismissed: true }, now, { quiet: false })).toBe(0)
    expect(reminderScore({ ...base, status: 'completed', due_at: now - 1 }, now, { quiet: false })).toBe(0)
    expect(reminderScore({ ...base, due_at: now - 1 }, now, { quiet: true })).toBe(0)
  })

  it('quiet hours gate matches backend semantics', () => {
    expect(inQuietHours(now, 22, 7, 23)).toBe(true)
    expect(inQuietHours(now, 22, 7, 6)).toBe(true)
    expect(inQuietHours(now, 22, 7, 12)).toBe(false)
  })
})

describe('task buckets', () => {
  const now = MON + 12 * 3600000
  it('classifies waiting/inbox/overdue/today', () => {
    expect(taskBucket({ status: 'waiting_input', due_at: 0 }, now)).toBe('waiting')
    expect(taskBucket({ status: 'inbox', due_at: 0 }, now)).toBe('inbox')
    expect(taskBucket({ status: 'planned', due_at: now - 1000 }, now)).toBe('overdue')
    expect(taskBucket({ status: 'planned', due_at: now + 3600000 }, now)).toBe('today')
    expect(taskBucket({ status: 'planned', due_at: now + 10 * DAY }, now)).toBe('all')
  })
})
