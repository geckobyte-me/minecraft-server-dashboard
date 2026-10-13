<template>
  <div class="settings-section">
    <div class="section-header">
      <span class="material-symbols-outlined">schedule</span>
      <h3>Uptime &amp; Activity</h3>
    </div>

    <div class="uptime-summary">
      <div class="summary-card">
        <div class="summary-label">Current Session</div>
        <div class="summary-value">{{ isOnline ? liveSessionText : 'Offline' }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">Total ({{ windowDays }}d)</div>
        <div class="summary-value">{{ totalText }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">Last Started</div>
        <div class="summary-value small">{{ lastStartedText }}</div>
      </div>
    </div>

    <div class="history">
      <div class="history-title">Recent start / stop events</div>
      <div v-if="loading" class="history-empty">Loading…</div>
      <div v-else-if="!events.length" class="history-empty">No activity recorded yet.</div>
      <ul v-else class="history-list">
        <li v-for="(e, i) in events.slice(0, 12)" :key="i" class="history-item">
          <span class="event-dot" :class="e.state" />
          <span class="event-state">{{ e.state === 'running' ? 'Started' : 'Stopped' }}</span>
          <span class="event-time">{{ formatEpoch(e.timestamp) }}</span>
          <span class="event-user">{{ e.actionUser }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useServerStore } from '../../stores/server'
import { formatDuration, formatHours, formatEpoch } from '../../utils/formatDuration'

const props = defineProps({
  serverId: { type: String, required: true },
  launchTime: { type: String, default: '' },
  isOnline: { type: Boolean, default: false }
})

const serverStore = useServerStore()

const loading = ref(false)
const events = ref([])
const totalRunningSeconds = ref(0)
const currentSessionSeconds = ref(0)
const windowDays = ref(30)
const now = ref(Date.now())

let ticker = null

// Live current-session uptime: server-reported session seconds plus wall-clock
// elapsed since we fetched, so the counter advances while the page is open.
const fetchedAt = ref(Date.now())
const liveSessionSeconds = computed(() => {
  if (!props.isOnline) return 0
  const elapsed = Math.floor((now.value - fetchedAt.value) / 1000)
  return currentSessionSeconds.value + Math.max(0, elapsed)
})
const liveSessionText = computed(() => formatDuration(liveSessionSeconds.value))
const totalText = computed(() => formatHours(totalRunningSeconds.value + (props.isOnline ? liveSessionSeconds.value - currentSessionSeconds.value : 0)))

const lastStartedText = computed(() => {
  const started = events.value.find(e => e.state === 'running')
  return started ? formatEpoch(started.timestamp) : '-'
})

const loadUptime = async () => {
  if (!props.serverId) return
  loading.value = true
  try {
    const data = await serverStore.getServerUptime(props.serverId, 30)
    if (data) {
      events.value = data.events || []
      totalRunningSeconds.value = data.totalRunningSeconds || 0
      currentSessionSeconds.value = data.currentSessionSeconds || 0
      windowDays.value = data.windowDays || 30
      fetchedAt.value = Date.now()
    }
  } catch (e) {
    console.error('Failed to load uptime:', e)
  } finally {
    loading.value = false
  }
}

watch(() => props.serverId, loadUptime)
watch(() => props.isOnline, loadUptime)

onMounted(() => {
  loadUptime()
  ticker = setInterval(() => { now.value = Date.now() }, 1000)
})

onUnmounted(() => {
  if (ticker) clearInterval(ticker)
})
</script>

<style scoped>
.settings-section {
  margin-bottom: 48px;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e5e5e5;
}
.section-header .material-symbols-outlined {
  font-size: 20px;
  color: #171717;
}
.section-header h3 {
  font-size: 16px;
  font-weight: 500;
  color: #171717;
  margin: 0;
}
.uptime-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}
.summary-card {
  padding: 20px;
  border: 1px solid #e5e5e5;
  border-radius: 8px;
}
.summary-label {
  font-size: 11px;
  font-weight: 500;
  color: #a3a3a3;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}
.summary-value {
  font-size: 24px;
  font-weight: 300;
  color: #171717;
  font-variant-numeric: tabular-nums;
}
.summary-value.small {
  font-size: 15px;
  font-weight: 400;
}
.history {
  border: 1px solid #e5e5e5;
  border-radius: 8px;
  padding: 20px;
}
.history-title {
  font-size: 11px;
  font-weight: 500;
  color: #a3a3a3;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 16px;
}
.history-empty {
  font-size: 13px;
  color: #a3a3a3;
  padding: 8px 0;
}
.history-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.history-item {
  display: grid;
  grid-template-columns: 12px 80px 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #f5f5f5;
  font-size: 13px;
}
.history-item:last-child {
  border-bottom: none;
}
.event-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #d4d4d4;
}
.event-dot.running {
  background: #22c55e;
}
.event-dot.stopped {
  background: #ef4444;
}
.event-state {
  color: #171717;
  font-weight: 500;
}
.event-time {
  color: #737373;
}
.event-user {
  color: #a3a3a3;
  font-family: monospace;
  font-size: 12px;
}
</style>
