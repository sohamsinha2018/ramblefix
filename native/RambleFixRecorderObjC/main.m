#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>

static void emit(NSDictionary *payload) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:payload options:0 error:nil];
    if (!data) { return; }
    NSString *line = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (!line) { return; }
    fprintf(stdout, "%s\n", [line UTF8String]);
    fflush(stdout);
}

static double linearFromDb(float db) {
    if (db <= -120.0f) { return 0.0; }
    return pow(10.0, db / 20.0);
}

static BOOL requestMicAccess(void) {
    dispatch_semaphore_t sema = dispatch_semaphore_create(0);
    __block BOOL granted = NO;
    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio completionHandler:^(BOOL allowed) {
        granted = allowed;
        dispatch_semaphore_signal(sema);
    }];
    dispatch_semaphore_wait(sema, DISPATCH_TIME_FOREVER);
    return granted;
}

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        double seconds = 10.0;
        double progressInterval = 0.25;
        NSString *output = @"recordings/native.wav";

        for (int i = 1; i < argc; i++) {
            NSString *arg = [NSString stringWithUTF8String:argv[i]];
            if ([arg isEqualToString:@"--seconds"] && i + 1 < argc) {
                seconds = atof(argv[++i]);
            } else if ([arg isEqualToString:@"--output"] && i + 1 < argc) {
                output = [NSString stringWithUTF8String:argv[++i]];
            } else if ([arg isEqualToString:@"--progress-interval"] && i + 1 < argc) {
                progressInterval = atof(argv[++i]);
            } else if ([arg isEqualToString:@"--help"] || [arg isEqualToString:@"-h"]) {
                printf("Usage: ramblefix-recorder --seconds 10 --output recordings/native.wav [--progress-interval 0.25]\n");
                return 0;
            }
        }

        if (seconds <= 0.0) {
            emit(@{@"event": @"error", @"error": @"seconds must be positive"});
            return 1;
        }

        if (!requestMicAccess()) {
            emit(@{@"event": @"error", @"error": @"microphone permission denied"});
            return 1;
        }

        NSURL *url = [NSURL fileURLWithPath:output];
        [[NSFileManager defaultManager] createDirectoryAtURL:[url URLByDeletingLastPathComponent]
                                 withIntermediateDirectories:YES
                                                  attributes:nil
                                                       error:nil];

        NSDictionary *settings = @{
            AVFormatIDKey: @(kAudioFormatLinearPCM),
            AVSampleRateKey: @(16000.0),
            AVNumberOfChannelsKey: @(1),
            AVLinearPCMBitDepthKey: @(16),
            AVLinearPCMIsFloatKey: @NO,
            AVLinearPCMIsBigEndianKey: @NO
        };

        NSError *error = nil;
        AVAudioRecorder *recorder = [[AVAudioRecorder alloc] initWithURL:url settings:settings error:&error];
        if (!recorder || error) {
            emit(@{@"event": @"error", @"error": error ? [error localizedDescription] : @"failed to create recorder"});
            return 1;
        }

        recorder.meteringEnabled = YES;
        if (![recorder prepareToRecord] || ![recorder record]) {
            emit(@{@"event": @"error", @"error": @"failed to start recorder"});
            return 1;
        }

        NSDate *start = [NSDate date];
        NSDate *lastProgress = start;
        emit(@{
            @"event": @"start",
            @"seconds": @(seconds),
            @"output": [url path],
            @"sampleRate": @16000,
            @"channels": @1
        });

        while ([[NSDate date] timeIntervalSinceDate:start] < seconds) {
            [NSThread sleepForTimeInterval:0.03];
            NSDate *now = [NSDate date];
            if ([now timeIntervalSinceDate:lastProgress] >= progressInterval) {
                [recorder updateMeters];
                float avgDb = [recorder averagePowerForChannel:0];
                float peakDb = [recorder peakPowerForChannel:0];
                emit(@{
                    @"event": @"level",
                    @"elapsed": @([now timeIntervalSinceDate:start]),
                    @"rms": @(linearFromDb(avgDb)),
                    @"peak": @(linearFromDb(peakDb)),
                    @"avgDb": @(avgDb),
                    @"peakDb": @(peakDb)
                });
                lastProgress = now;
            }
        }

        [recorder stop];
        emit(@{
            @"event": @"complete",
            @"elapsed": @([[NSDate date] timeIntervalSinceDate:start]),
            @"output": [url path]
        });
    }
    return 0;
}
