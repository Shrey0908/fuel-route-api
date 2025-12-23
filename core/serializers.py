from rest_framework import serializers

class LatLngSerializer(serializers.Serializer):
    lat = serializers.FloatField()
    lon = serializers.FloatField()

class RoutePlanRequestSerializer(serializers.Serializer):
    start = serializers.CharField(required=False)
    end = serializers.CharField(required=False)
    start_latlng = LatLngSerializer(required=False)
    end_latlng = LatLngSerializer(required=False)

    corridor_miles = serializers.FloatField(required=False, default=10.0)
    max_range_miles = serializers.FloatField(required=False, default=500.0)
    mpg = serializers.FloatField(required=False, default=10.0)

    def validate(self, attrs):
        if not (("start" in attrs) or ("start_latlng" in attrs)):
            raise serializers.ValidationError("Provide start or start_latlng")
        if not (("end" in attrs) or ("end_latlng" in attrs)):
            raise serializers.ValidationError("Provide end or end_latlng")
        return attrs
