{{/*
Backend subchart helpers.
*/}}
{{- define "backend.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name }}
{{- end }}
