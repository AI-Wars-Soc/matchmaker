pipeline {
  agent any
  environment {
        HUB_ACCESS_TOKEN = credentials('docker-hub-access-token')
  }
  stages {
    stage('Build') {
      steps {
        sh 'docker build --pull --no-cache -t aiwarssoc/matchmaker:latest .'
      }
    }

    stage('Push') {
	  when {
	    branch 'main'
	  }
      steps {
        sh 'docker login --username joeoc2001 --password $HUB_ACCESS_TOKEN && docker push aiwarssoc/matchmaker:latest'
      }
    }

  }
}