import type { components } from './schema.generated'

export type ApiSchemas = components['schemas']
export type TripBriefCreate = ApiSchemas['TripBriefCreate']
export type RunSummary = ApiSchemas['RunSummary']
export type RunResultView = ApiSchemas['RunResultView']
export type Run = ApiSchemas['Run']
export type RunStatus = ApiSchemas['RunStatus']
export type Stage = ApiSchemas['Stage']
export type StageProgress = ApiSchemas['StageProgress']
export type RunCounts = ApiSchemas['RunCounts']
export type Itinerary = ApiSchemas['Itinerary']
export type ItineraryDay = ApiSchemas['ItineraryDay']
export type ItineraryItem = ApiSchemas['ItineraryItem']
export type Claim = ApiSchemas['Claim']
export type Evidence = ApiSchemas['Evidence']
export type Place = ApiSchemas['Place']
export type Verdict = ApiSchemas['Verdict']
export type ContentPiece = ApiSchemas['ContentPiece']
export type ContentSet = ApiSchemas['ContentSet']
export type Illustration = ApiSchemas['Illustration']
export type IllustratedContentSet = ApiSchemas['IllustratedContentSet']

export type ValidationIssue = ApiSchemas['ValidationError']

export const isTerminalStatus = (status: RunStatus) =>
  status === 'succeeded' || status === 'failed' || status === 'cancelled'
