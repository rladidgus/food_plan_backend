Health Sync Design

Goal
- Keep daily activity data fresh by syncing from mobile devices to the API.
- Minimize missing days and duplicates.

Data Flow
1) OS stores health data (HealthKit / Health Connect).
2) Mobile app reads OS data on schedule or user action.
3) App sends daily summaries to the API.
4) API upserts by source_record_id when available, otherwise by date+type.

Background Sync (Mobile)
- iOS
  - Enable Background App Refresh.
  - Use HealthKit background delivery to get notified of updates.
  - Schedule periodic refresh (e.g., once per day) and also sync on app open.
- Android
  - Use WorkManager with a periodic worker (e.g., every 6-12 hours).
  - Use Health Connect change APIs to trigger a sync when available.
  - Sync on app open as a fallback.

Sync Policy
- Always sync the last N days (e.g., 7-14 days) to cover missed background runs.
- Use activity_source_record_id when provided; if missing, use date+type.
- Store the last successful sync timestamp on device to limit scope.

Monitoring/Logging (Server)
- Log start/end with count, created, updated.
- Log validation errors and the client source when present.
- Track sync latency (client timestamp vs server sync time) if provided.

Suggested Client Payload Fields
- activity_date (YYYY-MM-DD)
- activity_type (lowercase, normalized)
- steps, active_kcal, total_kcal, workout_minutes, distance_meters
- activity_source, activity_source_device, activity_source_app
- activity_source_record_id (if available)
- activity_created_at, activity_updated_at (UTC preferred)

