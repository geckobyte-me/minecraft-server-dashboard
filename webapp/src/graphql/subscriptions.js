/* eslint-disable */
// this is an auto generated file. This will be overwritten

export const onPutServerMetric = /* GraphQL */ `
  subscription OnPutServerMetric($id: String!) {
    onPutServerMetric(id: $id) {
      id
      cpuStats
      networkStats
      memStats
    }
  }
`;
export const onChangeState = /* GraphQL */ `
  subscription OnChangeState {
    onChangeState {
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

export const onPutec2ActionValidatorStatus = /* GraphQL */ `
  subscription OnPutec2ActionValidatorStatus($id: String!) {
    onPutec2ActionValidatorStatus(id: $id) {
      id
      action
      status
      timestamp
      message
      userEmail
    }
  }
`;
