import android.content.Context;
import androidx.health.connect.client.HealthConnectClient;
import androidx.health.connect.client.permission.HealthPermission;
import androidx.health.connect.client.records.ActiveCaloriesBurnedRecord;
import androidx.health.connect.client.records.DistanceRecord;
import androidx.health.connect.client.records.StepsRecord;
import androidx.health.connect.client.request.AggregateRequest;
import androidx.health.connect.client.time.TimeRangeFilter;
import androidx.health.connect.client.units.Energy;
import androidx.health.connect.client.units.Length;

import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;

public class HealthConnectManager {
    private final HealthConnectClient client;
    private final Executor executor = Executors.newSingleThreadExecutor();

    public HealthConnectManager(Context context) {
        client = HealthConnectClient.getOrCreate(context);
    }

    public Set<String> getRequiredPermissions() {
        return new HashSet<>(Arrays.asList(
            HealthPermission.getReadPermission(StepsRecord.class),
            HealthPermission.getReadPermission(ActiveCaloriesBurnedRecord.class),
            HealthPermission.getReadPermission(DistanceRecord.class)
        ));
    }

    public void readDailySummary(int days, SummaryCallback callback) {
        executor.execute(() -> {
            List<Map<String, Object>> result = new ArrayList<>();
            ZoneId zone = ZoneId.systemDefault();

            for (int i = 0; i < days; i++) {
                LocalDate date = LocalDate.now(zone).minusDays(i);
                Instant start = date.atStartOfDay(zone).toInstant();
                Instant end = date.plusDays(1).atStartOfDay(zone).toInstant();

                Long steps = client.aggregate(
                    new AggregateRequest(
                        Collections.singleton(StepsRecord.COUNT_TOTAL),
                        TimeRangeFilter.between(start, end)
                    )
                ).get(StepsRecord.COUNT_TOTAL);

                Energy kcalEnergy = client.aggregate(
                    new AggregateRequest(
                        Collections.singleton(ActiveCaloriesBurnedRecord.ENERGY_TOTAL),
                        TimeRangeFilter.between(start, end)
                    )
                ).get(ActiveCaloriesBurnedRecord.ENERGY_TOTAL);

                Length distance = client.aggregate(
                    new AggregateRequest(
                        Collections.singleton(DistanceRecord.DISTANCE_TOTAL),
                        TimeRangeFilter.between(start, end)
                    )
                ).get(DistanceRecord.DISTANCE_TOTAL);

                Map<String, Object> item = new HashMap<>();
                item.put("activity_date", date.toString());
                item.put("activity_type", "daily");
                item.put("steps", steps == null ? 0 : steps.intValue());
                item.put("active_kcal", kcalEnergy == null ? 0.0 : kcalEnergy.getInKilocalories());
                item.put("distance_meters", distance == null ? 0.0 : distance.getInMeters());
                result.add(item);
            }

            callback.onResult(result);
        });
    }

    public interface SummaryCallback {
        void onResult(List<Map<String, Object>> summary);
    }
}
