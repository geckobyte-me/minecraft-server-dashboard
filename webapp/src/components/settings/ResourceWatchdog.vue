<template>
  <div class="watchdog-card">
    <div class="card-header">
      <div>
        <div class="card-title">Resource Watchdog</div>
        <div class="card-subtitle">Auto-shutdown when idle</div>
      </div>
      <v-switch v-model="enabled" color="primary" hide-details inset density="compact" />
    </div>

    <div v-if="enabled" class="card-content">
      <div class="setting-row">
        <div class="setting-label">Metric</div>
      </div>
      <v-btn-toggle
        v-model="metric"
        mandatory
        density="compact"
        color="#171717"
        class="metric-toggle"
      >
        <v-btn value="CPUUtilization" size="small">CPU</v-btn>
        <v-btn value="NetworkOut" size="small">Network</v-btn>
      </v-btn-toggle>

      <template v-if="metric === 'CPUUtilization'">
        <div class="setting-row mt-4">
          <div class="setting-label">CPU Threshold</div>
          <div class="setting-value">{{ cpuThreshold }}%</div>
        </div>
        <v-slider
          v-model="cpuThreshold"
          :min="1"
          :max="100"
          color="#171717"
          track-color="#e5e5e5"
          hide-details
        />
      </template>

      <template v-else>
        <div class="setting-row mt-4">
          <div class="setting-label">Network Threshold</div>
        </div>
        <v-text-field
          v-model.number="networkThreshold"
          type="number"
          suffix="KB/min"
          variant="outlined"
          density="compact"
          hide-details
        />
      </template>

      <div class="setting-row mt-4">
        <div class="setting-label">Idle Duration</div>
        <v-text-field 
          v-model.number="evaluationPeriod"
          type="number" 
          suffix="min"
          variant="outlined"
          density="compact"
          hide-details 
        />
      </div>
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

// Network threshold is entered in KB/min and stored in bytes/min
const BYTES_PER_KB = 1024

const enabled = ref(false)
const metric = ref('CPUUtilization')
const cpuThreshold = ref(15)
const networkThreshold = ref(100)
const evaluationPeriod = ref(10)

let isUpdatingFromProps = false

// Initialize from props
watch(() => props.modelValue, (cfg) => {
  if (!cfg || isUpdatingFromProps) return
  isUpdatingFromProps = true

  // If alarmThreshold > 0, auto-shutdown is enabled
  enabled.value = cfg.alarmThreshold > 0
  metric.value = cfg.alarmMetric === 'NetworkOut' ? 'NetworkOut' : 'CPUUtilization'

  if (metric.value === 'NetworkOut') {
    networkThreshold.value = cfg.alarmThreshold
      ? Math.round(cfg.alarmThreshold / BYTES_PER_KB)
      : 100
  } else {
    cpuThreshold.value = Math.round(cfg.alarmThreshold) || 15
  }
  evaluationPeriod.value = Math.round(cfg.alarmEvaluationPeriod) || 10

  setTimeout(() => { isUpdatingFromProps = false }, 0)
}, { immediate: true })

// Emit changes
watch([enabled, metric, cpuThreshold, networkThreshold, evaluationPeriod], () => {
  if (isUpdatingFromProps) return

  let threshold = 0
  if (enabled.value) {
    threshold = metric.value === 'NetworkOut'
      ? (networkThreshold.value || 0) * BYTES_PER_KB
      : cpuThreshold.value
  }

  emit('update:modelValue', {
    ...props.modelValue,
    alarmMetric: metric.value,
    alarmThreshold: threshold,
    alarmEvaluationPeriod: evaluationPeriod.value
  })
})
</script>

<style scoped>
.watchdog-card {
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
}
.card-empty {
  padding: 32px 0;
  text-align: center;
  font-size: 13px;
  color: #a3a3a3;
}
.metric-toggle {
  margin-bottom: 4px;
}
.setting-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.setting-label {
  font-size: 13px;
  color: #737373;
}
.setting-value {
  font-size: 14px;
  font-weight: 500;
  color: #171717;
  font-family: monospace;
}
</style>
