<template>
  <div class="scheduler-card">
    <div class="card-header">
      <div>
        <div class="card-title">Task Scheduler</div>
        <div class="card-subtitle">Automatic start/stop times per weekday</div>
      </div>
      <v-switch v-model="enabled" color="primary" hide-details inset density="compact" />
    </div>

    <div v-if="enabled" class="card-content">
      <!-- Auto Start rules -->
      <div class="schedule-group">
        <div class="group-head">
          <div class="schedule-label">Auto Start</div>
          <v-btn
            size="x-small"
            variant="text"
            color="primary"
            prepend-icon="mdi-plus"
            @click="addRule(startRules)"
          >
            Add time
          </v-btn>
        </div>

        <div v-if="startRules.length === 0" class="rule-empty">
          No start times. Add one to schedule automatic starts.
        </div>

        <div v-for="(rule, i) in startRules" :key="`start-${i}`" class="rule-row">
          <v-text-field
            v-model="rule.time"
            type="time"
            variant="outlined"
            density="compact"
            hide-details
          />
          <v-select
            v-model="rule.days"
            :items="dayOptions"
            multiple
            chips
            closable-chips
            variant="outlined"
            density="compact"
            hide-details
            placeholder="Days"
          />
          <v-btn
            icon="mdi-delete-outline"
            size="small"
            variant="text"
            @click="removeRule(startRules, i)"
          />
        </div>
      </div>

      <!-- Auto Stop rules -->
      <div class="schedule-group">
        <div class="group-head">
          <div class="schedule-label">Auto Stop</div>
          <v-btn
            size="x-small"
            variant="text"
            color="primary"
            prepend-icon="mdi-plus"
            @click="addRule(stopRules)"
          >
            Add time
          </v-btn>
        </div>

        <div v-if="stopRules.length === 0" class="rule-empty">
          No stop times. Add one to schedule automatic stops.
        </div>

        <div v-for="(rule, i) in stopRules" :key="`stop-${i}`" class="rule-row">
          <v-text-field
            v-model="rule.time"
            type="time"
            variant="outlined"
            density="compact"
            hide-details
          />
          <v-select
            v-model="rule.days"
            :items="dayOptions"
            multiple
            chips
            closable-chips
            variant="outlined"
            density="compact"
            hide-details
            placeholder="Days"
          />
          <v-btn
            icon="mdi-delete-outline"
            size="small"
            variant="text"
            @click="removeRule(stopRules, i)"
          />
        </div>
      </div>

      <v-select
        v-model="timezone"
        :items="timezones"
        variant="outlined"
        density="compact"
        hide-details
        label="Timezone"
      />
    </div>

    <div v-else class="card-empty">
      Disabled
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({ modelValue: Object })
const emit = defineEmits(['update:modelValue'])

const enabled = ref(false)
// Each rule is { time: 'HH:MM', days: ['1','2',...] } (0=Sun ... 6=Sat)
const startRules = ref([])
const stopRules = ref([])
const timezone = ref('America/New_York')

const dayOptions = [
  { title: 'Mon', value: '1' },
  { title: 'Tue', value: '2' },
  { title: 'Wed', value: '3' },
  { title: 'Thu', value: '4' },
  { title: 'Fri', value: '5' },
  { title: 'Sat', value: '6' },
  { title: 'Sun', value: '0' },
]

const timezones = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'UTC'
]

// --- cron <-> rule conversion --------------------------------------------

// Parse a single 5-field cron into a { time, days } rule.
const parseCron = (cron) => {
  if (!cron || typeof cron !== 'string') return null
  const parts = cron.trim().split(/\s+/)
  if (parts.length < 5) return null
  const minute = parts[0].padStart(2, '0')
  const hour = parts[1].padStart(2, '0')
  const days =
    parts[4] === '*' || parts[4] === '?'
      ? ['0', '1', '2', '3', '4', '5', '6']
      : parts[4].split(',').map((d) => d.trim())
  return { time: `${hour}:${minute}`, days }
}

// A schedule expression can be a JSON array of crons (new) or a single cron
// string (legacy). Return an array of rules either way.
const parseSchedule = (value) => {
  if (!value) return []
  if (Array.isArray(value)) {
    return value.map(parseCron).filter(Boolean)
  }
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return []
    if (trimmed.startsWith('[')) {
      try {
        const arr = JSON.parse(trimmed)
        if (Array.isArray(arr)) return arr.map(parseCron).filter(Boolean)
      } catch {
        // fall through to single-cron handling
      }
    }
    const rule = parseCron(trimmed)
    return rule ? [rule] : []
  }
  return []
}

// Build a single cron string from a rule.
const buildCron = (rule) => {
  if (!rule || !rule.time) return null
  const [hour, minute] = rule.time.split(':')
  const dayStr =
    !rule.days || rule.days.length === 0 || rule.days.length === 7
      ? '*'
      : [...rule.days].sort().join(',')
  return `${parseInt(minute)} ${parseInt(hour)} * * ${dayStr}`
}

// Serialize rules to the stored value: JSON array of crons (or '' when empty).
const serializeRules = (rules) => {
  const crons = rules.map(buildCron).filter(Boolean)
  if (crons.length === 0) return ''
  return JSON.stringify(crons)
}

const addRule = (rules) => {
  rules.push({ time: '', days: ['1', '2', '3', '4', '5'] })
}

const removeRule = (rules, index) => {
  rules.splice(index, 1)
}

let isUpdatingFromProps = false

// Initialize from config (only once / when it first arrives).
watch(
  () => props.modelValue,
  (cfg) => {
    if (!cfg || isUpdatingFromProps) return
    isUpdatingFromProps = true

    const start = parseSchedule(cfg.startScheduleExpression)
    const stop = parseSchedule(cfg.stopScheduleExpression)
    startRules.value = start
    stopRules.value = stop
    enabled.value = start.length > 0 || stop.length > 0
    if (cfg.timezone) timezone.value = cfg.timezone

    setTimeout(() => {
      isUpdatingFromProps = false
    }, 0)
  },
  { immediate: true, deep: false }
)

// Push changes back to parent config.
watch(
  [enabled, startRules, stopRules, timezone],
  () => {
    if (isUpdatingFromProps) return

    const update = { ...props.modelValue, timezone: timezone.value }
    if (enabled.value) {
      update.startScheduleExpression = serializeRules(startRules.value)
      update.stopScheduleExpression = serializeRules(stopRules.value)
    } else {
      update.startScheduleExpression = ''
      update.stopScheduleExpression = ''
    }
    emit('update:modelValue', update)
  },
  { deep: true }
)
</script>

<style scoped>
.scheduler-card {
  padding: 24px;
  border: 1px solid #e5e5e5;
  border-radius: 8px;
  height: 100%;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
}
.card-title {
  font-size: 16px;
  font-weight: 500;
  color: #171717;
  margin-bottom: 4px;
}
.card-subtitle {
  font-size: 13px;
  color: #737373;
}
.card-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.card-empty {
  padding: 32px 0;
  text-align: center;
  font-size: 13px;
  color: #a3a3a3;
}
.schedule-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.schedule-label {
  font-size: 11px;
  font-weight: 500;
  color: #a3a3a3;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.rule-empty {
  font-size: 12px;
  color: #a3a3a3;
  padding: 4px 0 8px;
}
.rule-row {
  display: grid;
  grid-template-columns: 120px 1fr 40px;
  gap: 12px;
  align-items: start;
}
</style>
