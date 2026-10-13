/* eslint-disable */
// this is an auto generated file. This will be overwritten

export const putServerMetric = /* GraphQL */ `
  mutation PutServerMetric($input: ServerMetricInput!) {
    putServerMetric(input: $input) {
      id
      cpuStats
      networkStats
      memStats
    }
  }
`;
export const changeServerState = /* GraphQL */ `
  mutation ChangeServerState($input: ServerInfoInput!) {
    changeServerState(input: $input) {
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
export const iamProfileManager = /* GraphQL */ `
  mutation iamProfileManager($instanceId: String!) {
    iamProfileManager(instanceId: $instanceId)
  }
`;
export const startServer = /* GraphQL */ `
  mutation StartServer($instanceId: String!) {
    startServer(instanceId: $instanceId) 
  }
`;
export const stopServer = /* GraphQL */ `
  mutation StopServer($instanceId: String!) {
    stopServer(instanceId: $instanceId) 
  }
`;

export const PutLogAudit = /* GraphQL */ `
  mutation putLogAudit(
    $input: LogAuditInput!
  ) {
    putLogAudit(input: $input) {
      id
      actionUser
      action
      instanceId
      expirationEpoch
      createdAt
    }
  }
`;

export const PutLogAuditWithInviteeEmail = /* GraphQL */ `
  mutation putLogAudit(
    $input: LogAuditInput!
  ) {
    putLogAudit(input: $input) {
      id
      actionUser
      action
      instanceId
      inviteeEmail
      expirationEpoch
      createdAt
    }
  }
`;

export const putServerConfig = /* GraphQL */ `
  mutation PutServerConfig($input: ServerConfigInput!) {
    putServerConfig(input: $input) {
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

export const updateServerConfig = /* GraphQL */ `
  mutation UpdateServerConfig($input: ServerConfigInput!) {
    updateServerConfig(input: $input) {
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

export const addUserToServer = /* GraphQL */ `
  mutation AddUserToServer($instanceId: String!, $userEmail: AWSEmail!) {
    addUserToServer(instanceId: $instanceId, userEmail: $userEmail)
  }
`;

export const verifyUserEmail = /* GraphQL */ `
  mutation VerifyUserEmail($id: String!) {
    verifyUserEmail(id: $id)
  }
`;

export const updateUserRole = /* GraphQL */ `
  mutation UpdateUserRole($input: UpdateUserRoleInput!) {
    updateUserRole(input: $input)
  }
`;

export const removeUserFromServer = /* GraphQL */ `
  mutation RemoveUserFromServer($userId: String!, $serverId: String!) {
    removeUserFromServer(userId: $userId, serverId: $serverId)
  }
`;

export const putec2ActionValidatorStatus = /* GraphQL */ `
  mutation Putec2ActionValidatorStatus($input: ec2ActionValidatorStatusInput!) {
    putec2ActionValidatorStatus(input: $input) {
      id
      action
      status
      timestamp
      message
      userEmail
    }
  }
`;
