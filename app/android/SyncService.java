import com.google.gson.Gson;
import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

import java.io.IOException;
import java.util.List;
import java.util.Map;

public class SyncService {
    // Example base URL (emulator). Use device IP or production domain on real devices.
    private static final String BASE_URL = "http://10.0.2.2:8000";
    private final OkHttpClient client = new OkHttpClient();
    private final Gson gson = new Gson();

    public void uploadHealthConnectDaily(List<Map<String, Object>> activities, int userNumber) {
        for (Map<String, Object> item : activities) {
            item.put("user_number", userNumber);
        }
        String json = gson.toJson(activities);

        RequestBody body = RequestBody.create(
            json, MediaType.parse("application/json")
        );

        Request request = new Request.Builder()
            .url(BASE_URL + "/api/health-connect/sync")
            .post(body)
            .build();

        client.newCall(request).enqueue(new Callback() {
            @Override public void onFailure(Call call, IOException e) {}
            @Override public void onResponse(Call call, Response response) throws IOException {}
        });
    }
}
