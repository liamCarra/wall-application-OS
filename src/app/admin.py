from django.contrib import admin
from .models import WallifyUser, AIGeneration

@admin.register(WallifyUser)
class WallifyUserAdmin(admin.ModelAdmin):
    list_display = ('id', 'username', 'firstname', 'lastname', 'email')
    search_fields = ('username', 'email', 'firstname', 'lastname')
    ordering = ('id',)

@admin.register(AIGeneration)
class AIGenerationAdmin(admin.ModelAdmin):
    list_display = ('id', 'username', 'prompt', 'created_at')
    search_fields = ('username', 'prompt')
    ordering = ('-created_at',) 