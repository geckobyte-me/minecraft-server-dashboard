/* eslint-disable */
// this is an auto generated file. This will be overwritten

export const ec2Discovery = /* GraphQL */ `
  query ec2Discovery {
    ec2Discovery {
      id
      name
      type
      userEmail
      state
      vCpus
      memSize
      diskSize
      launchTime
      publicIp
      iamStatus
    }
  }
`;
export const getLogAudit = /* GraphQL */ `
  query GetLogAudit($id: ID!) {
    getLogAudit(id: $id) {
      id
      actionUser
      action
      instanceId
      expirationEpoch
      createdAt
    }
  }
`;

export const getServerConfig = /* GraphQL */ `
query GetServerConfig($id: String!) {
  getServerConfig(id: $id) {
    id
    stopScheduleExpression
    startScheduleExpression
    alarmMetric
    alarmThreshold
    alarmEvaluationPeriod
    timezone
    hasCognitoGroup
    createdAt
    updatedAt
  }
}
`;

export const getServerUsers = /* GraphQL */ `
  query GetServerUsers($instanceId: String!) {
    getServerUsers(instanceId: $instanceId) {
      id
      email
      fullName
    }
  }
`;

export const getAdminUsers = /* GraphQL */ `
  query GetAdminUsers {
    getAdminUsers {
      id
      email
      fullName
    }
  }
`;

export const searchUserByEmail = /* GraphQL */ `
  query SearchUserByEmail($email: AWSEmail!) {
    searchUserByEmail(email: $email) {
      id
      email
      fullName
    }
  }
`;

export const ec2MetricsHandler = /* GraphQL */ `
  query ec2MetricsHandler($id: String!) {
    ec2MetricsHandler(id: $id) {
      id
      cpuStats
      networkStats
      memStats
    }
  }
`;

export const getServerUptime = /* GraphQL */ `
  query GetServerUptime($instanceId: String!, $days: Int) {
    getServerUptime(instanceId: $instanceId, days: $days) {
      id
      state
      currentSessionSeconds
      totalRunningSeconds
      windowDays
      events {
        timestamp
        state
        actionUser
      }
    }
  }
`;
