// @flow

export type WindowsEventlog = {
  id: number,
  providerName: string,
  // if available, the provide GUID
  providerId?: string,
  logName: string,
  // if available, the processId
  processId?: string,
  // if available, the threadId
  threadId?: string,
  machineName: string,
  // this is a Date object
  timeCreated: Date,
  levelDisplayName: string,
  message: string
}
