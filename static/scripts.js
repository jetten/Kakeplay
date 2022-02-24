// On click on search results link
// append event listeners to dynamic content is as follows
$(document).on('click','.tracklink',function(e){
  event.preventDefault();
  window.history.back();
  $.get( $(this).attr("href") )
    .done(function(data) {
      if(data != "")
        alert(data);
      window.clearTimeout(timer);
      timer = setTimeout(updateCurrent, 1000)
      updateQueue();
    })
    .fail(function(data) {
      if(data != "")
        alert(data['responseText']);
    });

    // Decrement credits
    $('.credits').html( Number($('#credit_saldo').text()) - Number($(this).data("credits")) );
});

// On press on search button
$("#searchform").submit(function(event) {
  event.preventDefault();

  // Make search result appear to user as a separate page, allow use of back button to exit search results
  $(".queue").css("display", "none");
  $("#currentlyPlaying").css("display", "none");
  $(".nav").css("display", "flex");
  window.history.pushState('forward', null, "#search/"+$("input").first().val());
  $(window).on('popstate', function() {
    $(".queue").css("display", "block");
    $("#currentlyPlaying").css("display", "block");
    $(".nav").css("display", "none");
    $("#spotify-results").html("");
    $("#mp3-results").html("");
  });

  // Search Spotify
  $( "#spotify-results" ).html("");
  $.post( "search", $("input").first().val())
    .done(function(data) {
      for(var key in data['results']) {
        //$( ".result" ).append('<a href="" id="'+data['results'][key]['id']+'" class="tracklink">'
        $( "#spotify-results" ).append('<div class="songresult">'
        +'<img class="songresult_img" src="'+data['results'][key]['image'][2]['url']+'" />'
        +'<div class="credits credits_search">'+data['results'][key]['credits'] + '</div>'
        +'<a href="play_track/'+data['results'][key]['id'] + '" class="tracklink" data-credits="'+data['results'][key]['credits']+'">'
        +'<div class="songtitle" style="font-weight: bold;">'+data['results'][key]['name'] + "</div>"
        +'<div class="songartist">'+data['results'][key]['artist'] + '</div>'
        +'<div style="clear: left;"'
        + "</a></div>" );
      }
    })
    .fail(function(data) {
      if(data != "")
        alert(data['responseText']);
    });


    // Search MP3
    $( "#mp3-results" ).html("");
    $.post( "mpdsearch", $("input").first().val())
      .done(function(data) {
        for(var key in data['results']) {
          //$( ".result" ).append('<a href="" id="'+data['results'][key]['id']+'" class="tracklink">'
          $( "#mp3-results" ).append('<div class="songresult">'
          +'<img class="songresult_img" src="'+data['results'][key]['image'][2]['url']+'" />'
          +'<div class="credits credits_search">'+data['results'][key]['credits'] + '</div>'
          +'<a href="mpd_play_track?url='+data['results'][key]['id'] + '" class="tracklink" data-credits="'+data['results'][key]['credits']+'">'
          +'<div class="songtitle" style="font-weight: bold;">'+data['results'][key]['name'] + "</div>"
          +'<div class="songartist">'+data['results'][key]['artist'] + '</div>'
          +'<div style="clear: left;"'
          + "</a></div>" );
        }
      })
      .fail(function(data) {
        if(data != "")
          alert(data['responseText']);
      });
});

$("#volume").change(function(event) {
  $("#volumeindicator").html($("#volume").val());
  $.get( "volume/"+$("#volume").val() );
});


$(".mediacontrolbtn").click(function(event) {
  $.post("mediacontrol", {action: $(this).data("action")});
  timer = setTimeout(updateCurrent, 1000)
});

$(document).on('click','.deletesongbtn',function(e){
  //$.post("delete_track", {id: $(this).data("songid")});
  $(this).parent().css("opacity", "0.2")
  $(this).css("display", "none")
});


function updateQueue() {
  $( ".queue" ).html("");
  $.get( "queue")
    .done(function(data) {
      for(var key in data['queue']) {
        //$( ".result" ).append('<a href="" id="'+data['results'][key]['id']+'" class="tracklink">'
        $( ".queue" ).append('<div class="songresult">'
        +'<img class="songresult_img" src="'+data['queue'][key]['album']['images'][2]['url']+'" />'
        +'<div class="deletesongbtn credits_search" data-songid="'+data['queue'][key]['id']+'" style="cursor: pointer;"><i class="fas fa-trash"></i></div>'
        +'<div class="songtitle" style="font-weight: bold;">'+data['queue'][key]['name'] + "</div>"
        +'<div class="songartist">'+data['queue'][key]['artists'][0]['name'] + '</div>'
        +'<div style="clear: left;"'
        + "</div>" );
      }
    });
}
updateQueue();

var pos=0.0;
var playing=false;
var track_id=false;

function updateCurrent() {
  $.get("current")
    .done(function(data) {
      var volume = data['device']['volume_percent'];
      pos = Math.round(data['progress_ms']/1000);
      playing = data['is_playing'];
      var track_len = Math.round(data['item']['duration_ms']/1000);
      var track_name = data['item']['name'];
      var track_artist = data['item']['artists'][0]['name'];
      var devicename = data['device']['name'];
      var imagesrc = data['item']['album']['images'][0]['url'];
      if (track_id != data['item']['id']) {
        track_id = data['item']['id'];
        updateQueue();
      }

      if(devicename == SPOTIFY_PLAYBACK_DEVICE_NAME) {
        $("#volumeindicator").html(volume);
        $("#volume").val(volume);
        $("#currentlyPlaying").html('<img src="'+ imagesrc +'" style="width: 100%; max-width: 300px;"></img><br>');
        $("#currentlyPlaying").append(track_artist + ' - ' + track_name + ' (<span id="pos">'+ secondsToMinutes(pos) +'</span>/' + secondsToMinutes(track_len) + ')');
      }

      var nextUpdateIn = Math.min( 60000, (track_len-pos)*1000+2000  );
      if(nextUpdateIn<5000) {
        console.log("nextUpdateIn "+str(nextUpdateIn)+", setting to 5000");
        nextUpdateIn = 5000;
      }
      timer = setTimeout(updateCurrent, nextUpdateIn);
    });
}
var timer = setTimeout(updateCurrent, 0);
setTimeout(proceedTrackPos, 1000);
proceedtrackpos_last_timestamp = Date.now();

function proceedTrackPos() {
  if(playing) {
    //pos = pos+1;
    pos = pos + (Date.now()-proceedtrackpos_last_timestamp)/1000;
  }
  $("#pos").html(secondsToMinutes(pos));
  proceedtrackpos_last_timestamp = Date.now();
  setTimeout(proceedTrackPos, 1000);
}

function secondsToMinutes(pos) {
  return(Math.floor(pos/60) + ":" + String( Math.round(pos)%60 ).padStart(2, '0'))
}
