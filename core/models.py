from django.db import models

class FuelStop(models.Model):
    opis_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=2)
    rack_id = models.IntegerField()
    price = models.DecimalField(max_digits=8, decimal_places=4)

    # lat/lon baad me geocoding se fill honge
    lat = models.FloatField(null=True, blank=True, db_index=True)
    lon = models.FloatField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "city"]),
            models.Index(fields=["lat", "lon"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state})"
