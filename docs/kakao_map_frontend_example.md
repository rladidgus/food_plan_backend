# Kakao Map Frontend Example (Diet Plan Places)

This example shows how to:
- get the user's current location
- call `/api/diet-plan/places`
- render Kakao Map markers for the returned places

## Prerequisites
- Kakao JavaScript key (not REST key)
- Backend running with `KAKAO_REST_API_KEY` set
- `FRONTEND_ORIGINS` on the backend includes your frontend origin

## Minimal HTML Example

```html
<!DOCTYPE html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Diet Plan Places</title>
    <style>
      body { margin: 0; font-family: sans-serif; }
      #map { width: 100%; height: 70vh; }
      #controls { padding: 12px; display: flex; gap: 8px; }
      #food { flex: 1; padding: 8px; }
      #btn { padding: 8px 12px; }
    </style>
    <script src="//dapi.kakao.com/v2/maps/sdk.js?appkey=YOUR_JS_KEY"></script>
  </head>
  <body>
    <div id="controls">
      <input id="food" placeholder="예: 김밥, 샐러드, 치킨" />
      <button id="btn">주변 가게 찾기</button>
    </div>
    <div id="map"></div>

    <script>
      const mapContainer = document.getElementById("map");
      const map = new kakao.maps.Map(mapContainer, {
        center: new kakao.maps.LatLng(37.5665, 126.9780),
        level: 4,
      });

      const markers = [];
      const infoWindow = new kakao.maps.InfoWindow({ zIndex: 1 });

      function clearMarkers() {
        while (markers.length) {
          const m = markers.pop();
          m.setMap(null);
        }
      }

      function addMarker(place) {
        const position = new kakao.maps.LatLng(place.y, place.x);
        const marker = new kakao.maps.Marker({ position });
        marker.setMap(map);
        markers.push(marker);

        kakao.maps.event.addListener(marker, "click", () => {
          const content = `
            <div style="padding:6px 8px; font-size:12px;">
              <strong>${place.name}</strong><br/>
              ${place.road_address_name || place.address_name || ""}<br/>
              거리: ${place.distance_m || "-"} m<br/>
              ${place.place_url ? `<a href="${place.place_url}" target="_blank">상세보기</a>` : ""}
            </div>
          `;
          infoWindow.setContent(content);
          infoWindow.open(map, marker);
        });
      }

      async function fetchPlaces() {
        const foodName = document.getElementById("food").value.trim();
        if (!navigator.geolocation) {
          alert("브라우저에서 위치 정보를 사용할 수 없습니다.");
          return;
        }

        navigator.geolocation.getCurrentPosition(async (pos) => {
          const lat = pos.coords.latitude;
          const lng = pos.coords.longitude;
          const accessToken = "YOUR_SUPABASE_ACCESS_TOKEN";

          const res = await fetch("http://localhost:8000/api/diet-plan/places", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Authorization": `Bearer ${accessToken}`
            },
            credentials: "include",
            body: JSON.stringify({
              food_name: foodName || null,
              lat,
              lng,
              radius_m: 2000,
            }),
          });
          if (!res.ok) {
            alert("가게 검색 실패 (토큰/위치/서버 상태 확인)");
            return;
          }
          const data = await res.json();

          clearMarkers();
          const list = data.places || [];
          if (list.length) {
            map.setCenter(new kakao.maps.LatLng(list[0].y, list[0].x));
          }
          list.forEach(addMarker);
        }, () => alert("위치 권한이 필요합니다."));
      }

      document.getElementById("btn").addEventListener("click", fetchPlaces);
    </script>
  </body>
</html>
```

## Notes
- Use the Kakao JavaScript key in the SDK script tag.
- This example calls the backend at `http://localhost:8000`. Change it to your API host.
- If you see CORS errors, add your frontend origin to `FRONTEND_ORIGINS` on the backend.
