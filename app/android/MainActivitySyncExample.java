import android.os.Bundle;
import android.content.Intent;
import androidx.appcompat.app.AppCompatActivity;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.health.connect.client.HealthConnectClient;
import androidx.health.connect.client.permission.HealthPermission;
import androidx.health.connect.client.permission.PermissionController;

import java.util.List;
import java.util.Map;
import java.util.Set;

public class MainActivitySyncExample extends AppCompatActivity {
    private HealthConnectManager health;
    private SyncService sync;
    private ActivityResultLauncher<Set<String>> permissionLauncher;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        health = new HealthConnectManager(this);
        sync = new SyncService();

        permissionLauncher = registerForActivityResult(
            PermissionController.createRequestPermissionResultContract(),
            granted -> {
                if (granted.containsAll(health.getRequiredPermissions())) {
                    startSync();
                }
            }
        );

        int status = HealthConnectClient.getSdkStatus(this);
        if (status == HealthConnectClient.SDK_UNAVAILABLE) {
            return;
        }
        if (status == HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED) {
            Intent intent = HealthConnectClient.getSdkStatusIntent(this);
            if (intent != null) {
                startActivity(intent);
            }
            return;
        }

        requestPermissions();
    }

    @Override
    protected void onStart() {
        super.onStart();
        requestPermissions();
    }

    private void requestPermissions() {
        Set<String> permissions = health.getRequiredPermissions();
        permissionLauncher.launch(permissions);
    }

    private void startSync() {
        health.readDailySummary(7, (List<Map<String, Object>> summary) -> {
            sync.uploadHealthConnectDaily(summary, 1);
        });
    }
}
