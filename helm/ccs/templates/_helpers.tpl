{{/*
Construct DATABASE_URL from postgresql values.
*/}}
{{- define "ccs.databaseUrl" -}}
{{- $pg := .Values.postgresql -}}
{{- $user := $pg.auth.username -}}
{{- $pass := $pg.auth.password -}}
{{- $db := $pg.auth.database -}}
{{- $host := printf "%s-postgresql" .Release.Name -}}
{{- $port := 5432 -}}
{{- printf "postgresql://%s:%s@%s:%d/%s" $user $pass $host $port $db -}}
{{- end }}
