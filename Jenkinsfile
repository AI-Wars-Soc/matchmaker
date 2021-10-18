pipeline {
  agent any
  stages {
    stage('Build') {
      steps {
        sh 'docker build --pull --no-cache -t aiwarssoc/matchmaker:latest .'
      }
    }

    stage('Push') {
      steps {
        sh 'docker push aiwarssoc/matchmaker:latest'
      }
    }

  }
}