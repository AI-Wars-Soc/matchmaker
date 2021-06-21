pipeline {
  agent any
  stages {
    stage('Build') {
      steps {
        sh 'docker build -t aiwarssoc/matchmaker:latest .'
      }
    }

    stage('Push') {
      steps {
        sh 'docker push aiwarssoc/matchmaker:latest'
      }
    }

  }
}